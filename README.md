
# BitPacking Project (Student-style submission)
Author: Nathan Gallard
Date: 2025-11-02

## Project description
This project implements bit-packing compression methods for integer arrays:
- PackedCross: writes values tightly and allows spanning across 32-bit words.
- PackedAligned: keeps values aligned, never spanning words.
- OverflowPacking: uses a small-k main area and an overflow area for large values.

It includes:
- `src/bitpacking.py` : core implementation
- `bench.py` : simple benchmark runner
- `report.md` : project report (also attempt PDF)
- A README and sample usage.

## Requirements
- Python 3.8+
- No external dependencies required for the code itself.
- `reportlab` optional to generate PDF automatically.

## How to use
1. Run quick benchmark:
   ```bash
   python3 bench.py
   ```
2. Import and use in code:
   ```py
   from src.bitpacking import CompressionFactory
   compressor = CompressionFactory.create('cross', k=12)
   compressed = compressor.compress([1,2,3,1024,4,5,2048])
   out = []
   compressor.decompress(compressed, out)
   print(out)
   print(compressor.get(compressed, 3))
   ```

## Notes
- The code uses a simple header format; for a production project you'd use clearer serialization.
- The decompression and get functions assume the header is present.
- Negative numbers: optional zigzag encoding supported by passing use_zigzag=True to factory.

