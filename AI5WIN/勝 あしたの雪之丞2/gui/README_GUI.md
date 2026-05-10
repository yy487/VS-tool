# AI5WIN Integrated Tools GUI

## 运行方式

```bash
python ai5win_gui.py
```

需要 Pillow：

```bash
pip install pillow
```

## 集成内容

GUI 只是集成入口，不重写底层算法。各功能仍调用同目录下原本的命令行脚本，因此原有工具可继续单独在终端使用。

分页如下：

1. **ARC 封包/解包**
   - `ai5win_arc_tool.py unpack`
   - `ai5win_arc_tool.py pack`

2. **MES 文本**
   - `ai5win_mes_extract.py`
   - `ai5win_mes_inject.py --map replace_map.json`

3. **字库/映射流程**
   - `scan_chars.py`
   - `hanzi_replacer.py`
   - `font_gen.py`

4. **G24 图像**
   - 查看 G24 信息
   - G24 -> PNG：支持单文件，也支持 G24 目录批量转换到 PNG 目录。
   - PNG -> G24：支持单文件，也支持 PNG 目录批量转换到 G24 目录。
   - PNG -> G24 页面保留“参考原 G24/目录”输入：单文件沿用该 G24 的 x/y；目录批量时按相对路径/同名 G24 逐个读取 x/y。
   - Roundtrip 测试

5. **MSK 图层**
   - MSK 信息
   - MSK -> PNG
   - 原始 G24 + MSK 图层合成 RGBA
   - PNG -> MSK：灰度 PNG 直接编码为 MSK，支持单文件和目录批量。
   - RGBA Alpha -> MSK：从 RGBA PNG 提取 alpha 生成 MSK，支持单文件和目录批量。
   - TITLE_PT_M 特殊三切片拼接
   - MSK 页面提供“MSK 图层”“参考 G24/PNG/目录”“原始 G24”等独立路径输入，避免把原图和 mask 层混在一起。

6. **EXE Patch**
   - `patch_exe_font_banks.py`

7. **辅助工具**
   - `ai5win_disasm.py`
   - `verify_font_line.py`
   - 少量原始脚本快速调用入口

## 注意点

- G24 与 MSK 的批量入口已经改成“文件/目录”双按钮，不需要再手动粘贴目录路径。
- 所有任务在子线程中运行，输出统一显示在底部日志框。
- 如果要传带空格的复杂命令行参数，建议使用对应分页表单；“原始命令行”只做简单空格分割。
- `TITLE_PT_M.MSK` 不是普通一对一 alpha 图层，不能直接用“原始 G24 + MSK 图层合成 RGBA”。应先用 MSK 解码/拆分或特殊拼接功能处理。


## 本版修正点

- 补齐 `img_msk_tool.py` 的完整双向工作流：`MSK -> PNG`、`PNG -> MSK`、`RGBA Alpha -> MSK`、`TITLE_PT_M` 拆分/拼接。
- 补齐 `ai5win_g24_tools.py` 的批量目录工作流：`g24topng/g242png` 支持目录输入，`png2g24` 支持目录输入，并可用参考 G24 目录逐图读取原始坐标。
- GUI 的相关路径输入改成“文件 / 目录”双按钮，单文件和批量目录不再混用一个只能选文件的浏览按钮。
