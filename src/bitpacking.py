
"""BitPacking compression project
- Two packing strategies:
  * PackedCross (allows values to span two consecutive 32-bit words)
  * PackedAligned (values are aligned, never span two words)
- Overflow area support
- CompressionFactory to create compressors
- get(i) for direct access in compressed data
- Simple handling for negatives via zigzag encoding (bonus)
"""
from typing import List, Tuple
import math
import struct

WORD_BITS = 32
WORD_MASK = (1 << WORD_BITS) - 1

def _min_bits_needed(values: List[int]) -> int:
    """Return number of bits to represent max absolute value using zigzag if negatives present."""
    if not values:
        return 0
    return max((v.bit_length() for v in values))

# ZigZag for negative numbers (map signed -> unsigned)
def zigzag_encode(x: int) -> int:
    return (x << 1) ^ (x >> 31)

def zigzag_decode(z: int) -> int:
    return (z >> 1) ^ -(z & 1)

class PackedBase:
    def __init__(self, bits_per_value: int, use_zigzag: bool=False):
        assert 0 < bits_per_value <= WORD_BITS
        self.k = bits_per_value
        self.use_zigzag = use_zigzag

    def compress(self, arr: List[int]) -> List[int]:
        raise NotImplementedError

    def decompress(self, out: List[int]) -> None:
        raise NotImplementedError

    def get(self, compressed: List[int], idx: int) -> int:
        raise NotImplementedError

class PackedCross(PackedBase):
    """Allow values to cross word boundaries (packed tightly)."""
    def compress(self, arr: List[int]) -> List[int]:
        bitstream = 0
        bitlen = 0
        out = []
        for v in arr:
            val = zigzag_encode(v) if self.use_zigzag else v
            # mask
            val &= (1 << self.k) - 1
            bitstream |= (val << bitlen)
            bitlen += self.k
            while bitlen >= WORD_BITS:
                out.append(bitstream & WORD_MASK)
                bitstream >>= WORD_BITS
                bitlen -= WORD_BITS
        if bitlen:
            out.append(bitstream & WORD_MASK)
        # Store header: [k, length]
        header = [(self.k & 0xFFFF) | ((len(arr) & 0xFFFF) << 16)]
        return header + out

    def decompress(self, compressed: List[int], out: List[int]) -> None:
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
        while len(out) < length:
            if bitlen < k:
                if i_word < len(compressed):
                    bitstream |= (compressed[i_word] << bitlen)
                    bitlen += WORD_BITS
                    i_word += 1
                else:
                    # no more words
                    break
            val = bitstream & mask
            bitstream >>= k
            bitlen -= k
            if self.use_zigzag:
                val = zigzag_decode(val)
            out.append(val)

    def get(self, compressed: List[int], idx: int) -> int:
        header = compressed[0]
        k = header & 0xFFFF
        length = (header >> 16) & 0xFFFF
        assert 0 <= idx < length
        bitpos = idx * k
        word_idx = 1 + (bitpos // WORD_BITS)
        bit_off = bitpos % WORD_BITS
        # gather enough bits
        low = compressed[word_idx] if word_idx < len(compressed) else 0
        high = compressed[word_idx+1] if (word_idx+1) < len(compressed) else 0
        combined = (low | (high << WORD_BITS)) >> bit_off
        val = combined & ((1<<k)-1)
        return zigzag_decode(val) if self.use_zigzag else val

class PackedAligned(PackedBase):
    """Do not allow values to span words; each word stores floor(WORD_BITS / k) values."""
    def compress(self, arr: List[int]) -> List[int]:
        per_word = WORD_BITS // self.k
        out = []
        header = [(self.k & 0xFFFF) | ((len(arr) & 0xFFFF) << 16)]
        out.append(header[0])
        cur = 0
        used = 0
        for v in arr:
            val = zigzag_encode(v) if self.use_zigzag else v
            val &= (1<<self.k)-1
            cur |= (val << (used * self.k))
            used += 1
            if used == per_word:
                out.append(cur & WORD_MASK)
                cur = 0
                used = 0
        if used:
            out.append(cur & WORD_MASK)
        return out

    def decompress(self, compressed: List[int], out: List[int]) -> None:
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

class OverflowPacking:
    """Compress with overflow area for large values.
    Strategy:
      - Use small_k bits for regular values, reserve one special code (all ones) to indicate overflow.
      - Overflow area appended after main packed words, stored as full 32-bit words per overflow value.
      - Header encodes small_k and counts.
    """
    def __init__(self, small_k: int, use_zigzag: bool=False):
        assert 1 <= small_k < WORD_BITS
        self.small_k = small_k
        self.use_zigzag = use_zigzag

    def compress(self, arr: List[int]) -> List[int]:
        # decide which values fit in small_k, else mark overflow
        mask = (1 << self.small_k) - 2  # reserve all-ones for overflow marker
        out_main = []
        bitstream = 0
        bitlen = 0
        overflow = []
        for v in arr:
            val = zigzag_encode(v) if self.use_zigzag else v
            if val <= mask:
                token = val
            else:
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
        # header: small_k (8 bits), small_count (16), overflow_count (8)
        header0 = (self.small_k & 0xFF) | ((len(arr) & 0xFFFF)<<8) | ((len(overflow)&0xFF)<<24)
        out = [header0] + out_main + overflow
        return out

    def decompress(self, compressed: List[int], out: List[int]) -> None:
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
                # overflow: read from overflow area
                real = compressed[overflow_idx]
                overflow_idx += 1
                val = zigzag_decode(real) if self.use_zigzag else real
                out.append(val)
            else:
                val = zigzag_decode(token) if self.use_zigzag else token
                out.append(val)

    def get(self, compressed: List[int], idx: int) -> int:
        header0 = compressed[0]
        small_k = header0 & 0xFF
        length = (header0 >> 8) & 0xFFFF
        assert 0 <= idx < length
        mask = (1<<small_k)-1
        bitpos = idx * small_k
        word_idx = 1 + (bitpos // WORD_BITS)
        bit_off = bitpos % WORD_BITS
        low = compressed[word_idx] if word_idx < len(compressed) else 0
        high = compressed[word_idx+1] if word_idx+1 < len(compressed) else 0
        combined = (low | (high << WORD_BITS)) >> bit_off
        token = combined & mask
        if token == mask:
            # need to compute overflow index (count how many overflow tokens before idx)
            count = 0
            for i in range(idx):
                bpos = i * small_k
                w = 1 + (bpos // WORD_BITS)
                bo = bpos % WORD_BITS
                lw = compressed[w] if w < len(compressed) else 0
                hw = compressed[w+1] if w+1 < len(compressed) else 0
                comb = (lw | (hw << WORD_BITS)) >> bo
                t = comb & mask
                if t == mask:
                    count += 1
            overflow_start = 1 + max(0, (length*small_k + WORD_BITS -1)//WORD_BITS)
            real = compressed[overflow_start + count]
            return zigzag_decode(real) if self.use_zigzag else real
        else:
            return zigzag_decode(token) if self.use_zigzag else token

class CompressionFactory:
    @staticmethod
    def create(mode: str, k: int=None, small_k: int=None, use_zigzag: bool=False):
        if mode == 'cross':
            assert k is not None
            return PackedCross(k, use_zigzag=use_zigzag)
        if mode == 'aligned':
            assert k is not None
            return PackedAligned(k, use_zigzag=use_zigzag)
        if mode == 'overflow':
            assert small_k is not None
            return OverflowPacking(small_k, use_zigzag=use_zigzag)
        raise ValueError('unknown mode')
