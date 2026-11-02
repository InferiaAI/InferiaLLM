#!/usr/bin/env python3
"""Render a watermark-bearing SVG from ``run_kv_sweep.py`` output."""

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("comparison", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.comparison.read_text())
    if data.get("label") != "SIMULATED":
        raise ValueError("only explicitly SIMULATED sweep data can be rendered")
    rows = data["variants"]
    values = [row["ttft_p95_s"] for row in rows]
    width, height, left, bottom = 760, 430, 100, 70
    plot_h, plot_w = 270, 580
    maximum = max(values) * 1.1
    bars = []
    gap = plot_w / len(rows)
    for i, (row, value) in enumerate(zip(rows, values)):
        x = left + i * gap + 30
        bar_w = gap - 60
        bar_h = plot_h * value / maximum
        y = bottom + plot_h - bar_h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="#4c78a8"/>'
            f'<text x="{x + bar_w / 2:.1f}" y="{bottom + plot_h + 22}" text-anchor="middle">{row["variant"]}</text>'
            f'<text x="{x + bar_w / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle">{value:.1f}s</text>'
        )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/>
<text x="{left}" y="38" font-family="sans-serif" font-size="22" font-weight="bold">KV saturation policy sweep — TTFT p95</text>
<text x="{left}" y="58" font-family="sans-serif" font-size="13">Uncalibrated simulator stress result; lower is better</text>
<line x1="{left}" y1="{bottom}" x2="{left}" y2="{bottom + plot_h}" stroke="black"/>
<line x1="{left}" y1="{bottom + plot_h}" x2="{left + plot_w}" y2="{bottom + plot_h}" stroke="black"/>
<text x="30" y="{bottom + plot_h / 2}" transform="rotate(-90 30 {bottom + plot_h / 2})" font-family="sans-serif">seconds</text>
{''.join(bars)}
<text x="{width / 2}" y="{height - 14}" text-anchor="middle" font-family="sans-serif" font-size="28" font-weight="bold" fill="#b00020" opacity="0.75">SIMULATED — UNCALIBRATED</text>
</svg>'''
    args.out.write_text(svg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
