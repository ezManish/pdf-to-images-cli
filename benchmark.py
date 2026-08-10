"""
benchmark.py

Performance benchmarking script for pdf-to-images-cli.
Compares sequential vs parallel worker throughput on PDF page rendering.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from pdf_to_image import pdf_to_images

# Ensure UTF-8 output formatting for terminal compatibility
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run_benchmark(pdf_path: str = "Participation_Certificates.pdf", dpi: int = 200, format_ext: str = "png"):
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        print(f"Error: {pdf_file} not found for benchmark.")
        return

    import pymupdf
    doc = pymupdf.open(str(pdf_file))
    total_pages = doc.page_count
    doc.close()

    print("=" * 65)
    print(f"PDF-TO-IMAGES-CLI PERFORMANCE BENCHMARK")
    print(f"File: {pdf_file.name} | Total Pages: {total_pages} | Resolution: {dpi} DPI")
    print("=" * 65)

    worker_counts = [1, 2, 4, 8]
    results = []
    baseline_time = None

    for w in worker_counts:
        out_dir = Path("output") / f"benchmark_w{w}"
        start_time = time.perf_counter()
        
        pdf_to_images(
            pdf_path=pdf_file,
            output_dir=out_dir,
            fmt=format_ext,
            dpi=dpi,
            workers=w,
            show_progress=False,
        )
        
        elapsed = time.perf_counter() - start_time
        pages_per_sec = total_pages / elapsed

        if w == 1:
            baseline_time = elapsed
            speedup = 1.0
        else:
            speedup = baseline_time / elapsed if baseline_time else 1.0

        results.append({
            "workers": w,
            "mode": "Sequential" if w == 1 else f"Parallel ({w} workers)",
            "elapsed_sec": round(elapsed, 2),
            "pages_per_sec": round(pages_per_sec, 2),
            "speedup": round(speedup, 2),
        })

        print(f"  * Workers: {w:<2} | Time: {elapsed:>6.2f}s | Speed: {pages_per_sec:>6.2f} pages/sec | Speedup: {speedup:>5.2f}x")

    print("\n" + "=" * 65)
    print("BENCHMARK SUMMARY TABLE")
    print("=" * 65)
    header = f"| {'Workers':<8} | {'Mode':<22} | {'Time (s)':<10} | {'Pages/Sec':<11} | {'Speedup':<8} |"
    divider = "|-" + "-|-".join(["-"*8, "-"*22, "-"*10, "-"*11, "-"*8]) + "-|"
    print(header)
    print(divider)

    for r in results:
        row = f"| {r['workers']:<8} | {r['mode']:<22} | {r['elapsed_sec']:<10.2f} | {r['pages_per_sec']:<11.2f} | {r['speedup']:<7.2f}x |"
        print(row)
    print("=" * 65)

    # Save to JSON
    with open("benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump({
            "pdf_name": pdf_file.name,
            "total_pages": total_pages,
            "dpi": dpi,
            "results": results
        }, f, indent=2)

    print("\nBenchmark results saved to `benchmark_results.json`.")
    return results


if __name__ == "__main__":
    run_benchmark()
