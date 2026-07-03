"""Composite tiles + HUD into the debug preview grid."""
import cv2
import numpy as np

from modes import MODE_NAMES


def composite(tiles, mode, fps):
    labels = {
        1: ["person 1", "person 2", "person 3", "person 4"],
        2: ["quad TL", "quad TR", "quad BL", "track"],
        3: ["wide", "full", "waist", "face"],
    }[mode]
    cells = []
    for tile, label in zip(tiles, labels):
        cell = cv2.resize(tile, (960, 540))
        cv2.putText(cell, label, (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cells.append(cell)
    grid = np.vstack([np.hstack(cells[:2]), np.hstack(cells[2:])])
    cv2.putText(grid, f"[{MODE_NAMES[mode]}]  {fps:.0f} fps  keys: 1/2/3 mode, q quit",
                (16, 1060), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    return grid
