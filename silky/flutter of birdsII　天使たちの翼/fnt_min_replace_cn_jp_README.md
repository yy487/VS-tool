# fnt_min.igf 字符映射替换工具

适用于 Angel / flutter of birds 引擎的 `fnt_min.igf`。工具保持字体文件大小和 8448 个槽位不变，只把指定载体字符的 24x24 字模改画成中文字符。

## cn_jp.json 格式

推荐格式：

```json
{
  "过": "過",
  "这": "這",
  "间": "間"
}
```

含义：

- JSON 译文中的 `过` 会被替换成 CP932 可编码的 `過`。
- `fnt_min.igf` 中 `過` 对应的字模槽会被画成 `过`。
- 游戏运行时读取到的仍然是 `過`，但绘制出来看到的是 `过`。

也支持扩展格式：

```json
{
  "过": {"carrier": "過"},
  "这": {"to": "這"},
  "间": {"dst": "間"}
}
```

## 检查映射

```bat
python fnt_min_replace.py check-map cn_jp.json
```

会检查：

- key 是否为单个字符；
- carrier 是否为单个 CP932 可编码字符；
- carrier 是否落在 `fnt_min.igf` 的 8448 字符覆盖范围内；
- 是否有多个中文字符映射到同一个 carrier。

## 替换翻译 JSON

只处理 `name` 和 `message`，不会修改 `scr_msg`：

```bat
python fnt_min_replace.py apply-map-json json_trans cn_jp.json json_mapped --fields name,message
```

然后用 `json_mapped` 交给 `snc_inject.py` 注入。

## 生成替换后的 fnt_min.igf

```bat
python fnt_min_replace.py patch-font fnt_min.igf cn_jp.json fnt_min_chs.igf --ttf C:\Windows\Fonts\msyh.ttc
```

常用调整：

```bat
python fnt_min_replace.py patch-font fnt_min.igf cn_jp.json fnt_min_chs.igf --ttf C:\Windows\Fonts\msyh.ttc --size 22 --xoff 0 --yoff 1
```

## 预览

```bat
python fnt_min_replace.py preview-map fnt_min_chs.igf cn_jp.json preview.png --count 128
```

## 完整接入流程

1. 翻译 JSON，保留 `scr_msg`，修改 `name/message`。
2. 准备 `cn_jp.json`。
3. `apply-map-json` 把 JSON 中的中文替换为 CP932 载体字符。
4. `patch-font` 把载体字符字模改成中文。
5. `snc_inject.py` 用映射后的 JSON 注入 SNC。
6. 重封 `ANSNC.ifl`。
7. 把 `ANSYS.ifl` 里的 `fnt_min.igf` 替换为 `fnt_min_chs.igf` 后重封。
