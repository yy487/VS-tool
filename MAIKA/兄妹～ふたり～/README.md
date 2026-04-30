# AKB / ADB text tools

This is a first-pass extractor/injector for TWO.EXE-style `.ADB` scripts.

## Files

- `akb_op.py` — shared opcode template and binary parser.
- `akb_extract.py` — extract normal messages and choices to JSON.
- `akb_inject.py` — inject edited JSON and rebuild scripts with basic jump relocation.

## Extract

```bash
python akb_extract.py mes_full -o text.json
```

JSON fields:

```json
{
  "type": "msg",
  "file": "DAY01.ADB",
  "offset": 31,
  "line_id": "KDKF00282",
  "ctrl": "\\I7",
  "voice": "TOM0001",
  "name": "智美",
  "message": "「……お兄ちゃん、朝だよ。起きて……お兄ちゃん」",
  "translation": ""
}
```

Choices:

```json
{
  "type": "choice",
  "file": "RBH03_2.ADB",
  "offset": 18,
  "target": 101,
  "message": "可愛がる",
  "translation": ""
}
```

## Inject

Fill `translation`; then:

```bash
python akb_inject.py mes_full text.json -o mes_out
```

If you directly overwrite `message` instead of using `translation`:

```bash
python akb_inject.py mes_full text.json -o mes_out --use-message
```

Default encoding is `cp932`. If your hook uses another runtime encoding, pass for example:

```bash
python akb_inject.py mes_full text.json -o mes_out --encoding gbk
```

## Important

- Non-equal length injection rebuilds the whole script.
- Known offset fields are relocated:
  - `0x0001` choice target
  - `0x0002` direct jump
  - `0x0010`, `0x0016`, `0x0116` conditional jump style targets
  - `0x0040` offset style target
- Complex menu/system opcodes in `MAIN.ADB` are kept in the opcode template, but if a new unknown opcode appears, add it to `OPCODES` in `akb_op.py` instead of silently guessing.

`akb_extract.py` 默认会从未知 opcode 中恢复；调试 opcode 表时可加 `--strict`。
