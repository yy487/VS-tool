# vn-tool

A collection of reverse engineering and localization scripts for visual novels, accumulated while working on translation projects across various game engines.

## ⚠️ Important Notice

**Most scripts in this repository are written for a specific game, not as general-purpose tools.**

Although folders are organized by engine name (e.g. `AI6WIN`, `ADVWIN32`, `EntisGLS`), this does **not** mean the scripts will work on every game built with that engine. Even within the same engine, different titles typically differ in:

- Encryption/decryption keys, XOR constants, and permutation tables
- File header magic, version numbers, and struct field layouts
- Character encoding handling (SJIS / GB2312 / custom codepages)
- Script opcode coverage and parameter formats
- Resource compression parameters (LZSS window size, dictionary initialization, etc.)

As a result, **running these scripts directly against a different game will almost always fail**. You will need to adjust constants, table structures, and parsing logic to match your target. The value of this repository lies more in:

- Serving as a reference for analyzing specific engine formats
- Acting as a starting template when writing new tools
- Recording implementation details of particular games for future reference

## Repository Layout

Each top-level folder roughly corresponds to a game engine or a specific game. Subfolders are usually scripts adapted for one particular title under that engine. For example:

```
AI6WIN/
├── *.py                          # Engine-level parsing attempts (still not necessarily portable)
└── 麻呂の患者はガテン系/         # Scripts adjusted for this specific game
```

When in doubt, treat the engine-level scripts as **prototypes** and the game-specific subfolders as the actual working versions.

## Disclaimer

These scripts are shared for research and personal localization purposes. They are provided as-is, with no guarantee of correctness, completeness, or compatibility with any particular game. Use at your own discretion, and respect the copyright of the original works.
