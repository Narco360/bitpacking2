
import random, time
from src.bitpacking import CompressionFactory
import sys

def benchmark(mode, values, k=None, small_k=None, use_zigzag=False):
    comp = CompressionFactory.create(mode, k=k, small_k=small_k, use_zigzag=use_zigzag)
    t0 = time.perf_counter()
    compressed = comp.compress(values)
    t1 = time.perf_counter()
    out = []
    t2 = time.perf_counter()
    comp.decompress(compressed, out)
    t3 = time.perf_counter()
    # random access tests
    t_get0 = time.perf_counter()
    sample = [comp.get(compressed, i) for i in range(0, len(values), max(1, len(values)//100))]
    t_get1 = time.perf_counter()
    return {
        'mode': mode,
        'n': len(values),
        'k': k,
        'small_k': small_k,
        'compress_time': t1 - t0,
        'decompress_time': t3 - t2,
        'get_time': t_get1 - t_get0,
        'compressed_size_words': len(compressed)
    }

if __name__ == '__main__':
    sizes = [1000, 10000, 100000]
    results = []
    for n in sizes:
        # generate values with some large outliers
        vals = [random.randint(0, 15) for _ in range(n)]
        # add large at random positions
        for _ in range(max(1, n//1000)):
            vals[random.randrange(n)] = random.randint(0, 2**20)
        r = benchmark('cross', vals, k=12)
        results.append(r)
        r = benchmark('aligned', vals, k=12)
        results.append(r)
        r = benchmark('overflow', vals, small_k=4)
        results.append(r)
    import json
    print(json.dumps(results, indent=2))
