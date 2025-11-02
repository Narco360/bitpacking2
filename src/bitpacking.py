from typing import List, Tuple
import math
import struct

# Constantes pour définir le nombre de bits par mot (32 bits)
WORD_BITS = 32
WORD_MASK = (1 << WORD_BITS) - 1

def _min_bits_needed(values: List[int]) -> int:
    """Retourne le nombre minimal de bits nécessaires pour représenter le plus grand entier."""
    if not values:
        return 0
    return max((v.bit_length() for v in values))

# --- Encodage ZigZag (utile pour gérer les nombres négatifs sans perte d'ordre) ---

def zigzag_encode(x: int) -> int:
    """Transforme un entier signé en entier non signé (ZigZag)."""
    return (x << 1) ^ (x >> 31)

def zigzag_decode(z: int) -> int:
    """Inverse du ZigZag : récupère le signe original."""
    return (z >> 1) ^ -(z & 1)


# --- Classe de base commune à toutes les stratégies de compression ---

class PackedBase:
    def __init__(self, bits_per_value: int, use_zigzag: bool=False):
        assert 0 < bits_per_value <= WORD_BITS
        self.k = bits_per_value
        self.use_zigzag = use_zigzag

    def compress(self, arr: List[int]) -> List[int]:
        """À redéfinir : compresse le tableau d'entrée."""
        raise NotImplementedError

    def decompress(self, out: List[int]) -> None:
        """À redéfinir : décompresse et écrit le résultat dans 'out'."""
        raise NotImplementedError

    def get(self, compressed: List[int], idx: int) -> int:
        """Retourne la valeur d'indice idx sans décompresser tout le tableau."""
        raise NotImplementedError


# --- Version 1 : compression "cross" (peut traverser plusieurs mots de 32 bits) ---

class PackedCross(PackedBase):
    """Version compacte : permet aux valeurs de traverser les frontières de mots."""
    def compress(self, arr: List[int]) -> List[int]:
        bitstream = 0
        bitlen = 0
        out = []
        for v in arr:
            # Encodage ZigZag si activé
            val = zigzag_encode(v) if self.use_zigzag else v
            # On garde uniquement les k bits significatifs
            val &= (1 << self.k) - 1
            # On ajoute la valeur dans le flux binaire courant
            bitstream |= (val << bitlen)
            bitlen += self.k
            # Si on dépasse 32 bits, on vide dans la sortie
            while bitlen >= WORD_BITS:
                out.append(bitstream & WORD_MASK)
                bitstream >>= WORD_BITS
                bitlen -= WORD_BITS
        # Si il reste des bits non écrits, on les ajoute aussi
        if bitlen:
            out.append(bitstream & WORD_MASK)
        # Petit header : k + longueur
        header = [(self.k & 0xFFFF) | ((len(arr) & 0xFFFF) << 16)]
        return header + out

    def decompress(self, compressed: List[int], out: List[int]) -> None:
        """Reconstitue les valeurs à partir du flux binaire compressé."""
        if not compressed:
            return
        header = compressed[0]
        k = header & 0xFFFF
        length = (header >> 16) & 0xFFFF
        mask = (1 << k) - 1
        bitstream = 0
        bitlen = 0
        i_word = 1
        out.clear()
        # Lecture séquentielle des bits
        while len(out) < length:
            if bitlen < k:
                if i_word < len(compressed):
                    bitstream |= (compressed[i_word] << bitlen)
                    bitlen += WORD_BITS
                    i_word += 1
                else:
                    break
            val = bitstream & mask
            bitstream >>= k
            bitlen -= k
            if self.use_zigzag:
                val = zigzag_decode(val)
            out.append(val)

    def get(self, compressed: List[int], idx: int) -> int:
        """Accès direct à un élément compressé sans tout décompresser."""
        header = compressed[0]
        k = header & 0xFFFF
        length = (header >> 16) & 0xFFFF
        assert 0 <= idx < length
        bitpos = idx * k
        word_idx = 1 + (bitpos // WORD_BITS)
        bit_off = bitpos % WORD_BITS
        # On récupère les deux mots nécessaires (cas de chevauchement)
        low = compressed[word_idx] if word_idx < len(compressed) else 0
        high = compressed[word_idx+1] if (word_idx+1) < len(compressed) else 0
        combined = (low | (high << WORD_BITS)) >> bit_off
        val = combined & ((1<<k)-1)
        return zigzag_decode(val) if self.use_zigzag else val


# --- Version 2 : compression "alignée" (ne traverse jamais les frontières de mots) ---

class PackedAligned(PackedBase):
    """Chaque mot contient un nombre entier de valeurs (plus simple, mais un peu moins compact)."""
    def compress(self, arr: List[int]) -> List[int]:
        per_word = WORD_BITS // self.k  # nb d'entiers stockables dans un mot
        out = []
        header = [(self.k & 0xFFFF) | ((len(arr) & 0xFFFF) << 16)]
        out.append(header[0])
        cur = 0
        used = 0
        for v in arr:
            val = zigzag_encode(v) if self.use_zigzag else v
            val &= (1<<self.k)-1
            # On empile dans le mot courant
            cur |= (val << (used * self.k))
            used += 1
            if used == per_word:
                out.append(cur & WORD_MASK)
                cur = 0
                used = 0
        # On vide le dernier mot s’il est partiel
        if used:
            out.append(cur & WORD_MASK)
        return out

    def decompress(self, compressed: List[int], out: List[int]) -> None:
        """Relecture simple sans chevauchement."""
        header = compressed[0]
        k = header & 0xFFFF
        length = (header >> 16) & 0xFFFF
        per_word = WORD_BITS // k
        mask = (1<<k)-1
        out.clear()
        i = 1
        while len(out) < length and i < len(compressed):
            cur = compressed[i]
            for j in range(per_word):
                if len(out) >= length:
                    break
                val = cur & mask
                cur >>= k
                if k and val is not None:
                    if self.use_zigzag:
                        val = zigzag_decode(val)
                    out.append(val)
            i += 1

    def get(self, compressed: List[int], idx: int) -> int:
        """Accès direct optimisé (plus simple que la version cross)."""
        header = compressed[0]
        k = header & 0xFFFF
        length = (header >> 16) & 0xFFFF
        per_word = WORD_BITS // k
        assert 0 <= idx < length
        word_idx = 1 + (idx // per_word)
        pos_in_word = idx % per_word
        if word_idx >= len(compressed):
            return 0
        cur = compressed[word_idx]
        val = (cur >> (pos_in_word * k)) & ((1<<k)-1)
        return zigzag_decode(val) if self.use_zigzag else val


# --- Version 3 : compression avec zone de débordement ("overflow") ---

class OverflowPacking:
    """Compression avec zone de débordement pour les valeurs trop grandes.
    Idée :
      - On réserve un code spécial (tout à 1) pour signaler qu'une valeur réelle
        est stockée ailleurs, à la fin du tableau (overflow area).
    """
    def __init__(self, small_k: int, use_zigzag: bool=False):
        assert 1 <= small_k < WORD_BITS
        self.small_k = small_k
        self.use_zigzag = use_zigzag

    def compress(self, arr: List[int]) -> List[int]:
        # On détermine quelles valeurs tiennent dans small_k bits
        mask = (1 << self.small_k) - 2  # le dernier code est réservé à "overflow"
        out_main = []
        bitstream = 0
        bitlen = 0
        overflow = []
        for v in arr:
            val = zigzag_encode(v) if self.use_zigzag else v
            if val <= mask:
                token = val
            else:
                # Trop grand : on marque un overflow et on le stocke plus tard
                token = (1 << self.small_k) - 1
                overflow.append(val)
            bitstream |= (token << bitlen)
            bitlen += self.small_k
            while bitlen >= WORD_BITS:
                out_main.append(bitstream & WORD_MASK)
                bitstream >>= WORD_BITS
                bitlen -= WORD_BITS
        if bitlen:
            out_main.append(bitstream & WORD_MASK)
        # Header compact : contient small_k, taille et nombre d'overflows
        header0 = (self.small_k & 0xFF) | ((len(arr) & 0xFFFF)<<8) | ((len(overflow)&0xFF)<<24)
        out = [header0] + out_main + overflow
        return out

    def decompress(self, compressed: List[int], out: List[int]) -> None:
        """Relecture en tenant compte des valeurs stockées dans la zone overflow."""
        if not compressed:
            return
        header0 = compressed[0]
        small_k = header0 & 0xFF
        length = (header0 >> 8) & 0xFFFF
        overflow_count = (header0 >> 24) & 0xFF
        mask = (1<<small_k)-1
        out.clear()
        bitstream = 0
        bitlen = 0
        i_word = 1
        # Calcul de la position de début de la zone overflow
        overflow_idx = 1 + max(0, (length*small_k + WORD_BITS - 1)//WORD_BITS)
        while len(out) < length:
            if bitlen < small_k:
                if i_word < overflow_idx:
                    bitstream |= (compressed[i_word] << bitlen)
                    bitlen += WORD_BITS
                    i_word += 1
                else:
                    break
            token = bitstream & mask
            bitstream >>= small_k
            bitlen -= small_k
            if token == mask:
                # On récupère la vraie valeur depuis la zone overflow
                real = compressed[overflow_idx]
                overflow_idx += 1
                val = zigzag_decode(real) if self.use_zigzag else real
                out.append(val)
            else:
                val = zigzag_decode(token) if self.use_zigzag else token
                out.append(val)

    def get(self, compressed: List[int], idx: int) -> int:
        """Accès direct même en présence d'overflow (plus lent)."""
        header0 = compressed[0]
        small_k = header0 & 0xFF
        length = (header0 >> 8) & 0xFFFF
        assert 0 <= idx < length
        mask = (1<<small_k)-1
