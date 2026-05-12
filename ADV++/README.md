# kankin_font_tools_v3_complete

完整合并版，支持：

- `info`
- `scan`
- `patch`
- `convert-json`

默认只处理：

- `name`
- `message`

不会默认处理：

- `scr_msg`
- `pre_jp`
- `msg`

## 1. 扫描 JSON

```bat
python kankin_font_patch.py scan E:\BaiduNetdiskDownload\1\2\3\chs\json_restore --cn-jp E:\BaiduNetdiskDownload\1\2\3\subs_cn_jp.json --output scan_report.json
```

## 2. 重绘 font.dat

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json E:\BaiduNetdiskDownload\1\2\3\chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf --report font_patch_report.json
```

如果字体显示偏上/偏下，可加：

```bat
--y-offset 1
```

如果字太大/太小，可加：

```bat
--scale 1.0
```

或固定字号：

```bat
--font-size 18
```

## 3. 转换 JSON 为代用字版本

```bat
python kankin_font_patch.py convert-json E:\BaiduNetdiskDownload\1\2\3\chs\json_restore --cn-jp subs_cn_jp.json --output-dir E:\BaiduNetdiskDownload\1\2\3\chs\json_cn
```

## 4. 查看 font.dat

```bat
python kankin_font_patch.py info font.dat
```

## 兼容说明

`--cn-jp` 和 `--map` 等价。
