# ISM 引擎 / DATA.ISA 逆向关键函数整理

> 目标：记录 `hitoyume.exe` 与 `ism.dll` 中和游戏启动、ISA 封包注册、虚拟文件系统、插件加载相关的关键函数，方便后续继续做文本提取、封包重打包、散文件覆盖和脚本结构分析。
>
> 当前样本：
>
> - `hitoyume.exe.c`
> - `ism.dll.c`
> - `DATA.ISA` 文件头截图

---

## 1. 总体结论

`hitoyume.exe` 更像一个启动壳 / 宿主程序：它负责切换当前目录、加载 `ISM.DLL`、解析导出函数、创建主窗口，然后通过 ISM 引擎接口启动脚本。

真正的封包处理逻辑在 `ISM.DLL` 中，尤其是：

- `ISM_RegisterArchive`
- `FUN_10001280`
- `FUN_10023870`
- `FUN_10023830`
- `FUN_10024140`
- `FUN_100247a0`
- `FUN_100235e0`

启动主线大致是：

```text
hitoyume.exe
  -> LoadLibraryA("ISM.DLL")
  -> GetProcAddress("ISM_Initialize" / "ISM_StartScript" / "ISM_RegisterArchive" / ...)
  -> ISM_RegisterArchive("DATA.ISA")
  -> ISM_StartScript("SYSTEM.ISM")
  -> ISM.DLL 内部 VFS 打开 SYSTEM.ISM
  -> 优先散文件，失败后查 DATA.ISA 目录表
```

---

## 2. EXE 侧关键函数

### 2.1 `FUN_004011c0` -> `App_LoadISMAndResolveExports`

**建议重命名：**

```c
FUN_004011c0 -> App_LoadISMAndResolveExports
```

**功能：**

该函数是 EXE 侧加载 ISM 引擎的核心初始化函数。

主要行为：

1. 读取命令行路径；
2. 解析出 exe 所在目录；
3. `SetCurrentDirectoryA(local_400)`，将当前工作目录切换到游戏目录；
4. `LoadLibraryA("ISM.DLL")`；
5. 使用 `GetProcAddress` 解析大量 ISM 导出函数。

关键导出包括：

```c
ISM_Initialize
ISM_StartScript
ISM_StopScript
ISM_RegisterArchive
ISM_SetSearchPath
ISM_SetCdromDrive
ISM_RegisterFunction
ISM_Suspend
ISM_Resume
ISM_Finalize
ISM_GetDLLVersion
```

**逆向意义：**

这个函数证明 exe 本身不直接解析 `DATA.ISA`，它只是动态加载 `ISM.DLL` 并保存导出函数指针。后续 ISA 注册和脚本启动都通过这些函数指针完成。

---

### 2.2 `FUN_00403cc0` -> `App_MainWindowAndMessageLoop`

**建议重命名：**

```c
FUN_00403cc0 -> App_MainWindowAndMessageLoop
```

**功能：**

EXE 侧主窗口初始化和主流程入口。

主要行为：

1. 创建互斥体，防止多开；
2. 调用 `FUN_004011c0()` 加载 `ISM.DLL`；
3. 保存 HINSTANCE；
4. 设置启动脚本名：

```c
lstrcpyA(&DAT_0040dfb8, "SYSTEM.ISM");
```

5. 创建游戏主窗口；
6. 进入消息循环。

**逆向意义：**

这里确认游戏启动脚本是：

```text
SYSTEM.ISM
```

也就是说，`DATA.ISA` 只是资源 / 脚本封包，真正的执行入口脚本是封包或散文件中的 `SYSTEM.ISM`。

---

### 2.3 菜单 / 命令处理分支 -> `App_RestartScriptFromArchive`

**相关代码特征：**

```c
(*DAT_0040dfb4)();              // ISM_StopScript
(*DAT_0040d984)("DATA.ISA");   // ISM_RegisterArchive
(*DAT_0040d990)(&DAT_0040dfb8); // ISM_StartScript("SYSTEM.ISM")
```

**建议重命名：**

```c
相关分支 -> App_RestartScriptFromArchive
DAT_0040dfb4 -> pISM_StopScript
DAT_0040d984 -> pISM_RegisterArchive
DAT_0040d990 -> pISM_StartScript
DAT_0040dfb8 -> g_StartScriptName_SYSTEM_ISM
```

**功能：**

该分支像是回到标题、重新开始、菜单指令或状态切换后的脚本重启流程。

流程：

```text
停止当前脚本
注册 DATA.ISA
启动 SYSTEM.ISM
```

**逆向意义：**

这里直接确认了 `DATA.ISA` 的注册方式：

```c
ISM_RegisterArchive("DATA.ISA")
```

不是 exe 自己 `CreateFileA("DATA.ISA")` 后解析，而是把封包名交给 `ISM.DLL`。

---

### 2.4 `FUN_00404680` -> `App_RegisterHostCallbacks`

**建议重命名：**

```c
FUN_00404680 -> App_RegisterHostCallbacks
```

**功能：**

EXE 向 ISM 引擎注册宿主侧回调函数。

相关行为：

```c
ISM_RegisterFunction(&LAB_00403ff0, param_4);
ISM_RegisterFunction(&LAB_004040e0, param_5);
ISM_RegisterFunction(&LAB_00404270, param_6);
```

之后还会创建一个隐藏的 `ISM avi window`，用于 AVI / 视频播放一类的宿主窗口协作。

**逆向意义：**

如果后续脚本执行中遇到播放视频、菜单命令、外部函数调用，这几个回调位置需要重点看。文本提取阶段暂时不是最高优先级，但脚本行为分析时有价值。

---

## 3. ISM.DLL 导出层关键函数

### 3.1 `ISM_Initialize`

**地址 / 名称：**

```c
ISM_Initialize
```

**功能：**

ISM 引擎初始化入口。大致负责：

- 初始化运行环境；
- 保存窗口句柄、实例句柄和外部参数；
- 初始化内部状态；
- 可能触发插件、图形、音频等子系统准备。

**逆向意义：**

封包解包本身不依赖它，但从运行链上看，它是 EXE 调用 ISM 接口前的基础初始化。

---

### 3.2 `ISM_RegisterArchive`

**地址 / 名称：**

```c
ISM_RegisterArchive(char *param_1)
```

**功能：**

ISM 对外导出的封包注册接口。

反编译逻辑大致是：

```c
undefined4 ISM_RegisterArchive(char *param_1)
{
    if (DAT_100bd35c == 0) {
        return 1;
    }
    return FUN_10001280(param_1);
}
```

**调用链：**

```text
EXE: pISM_RegisterArchive("DATA.ISA")
  -> ISM_RegisterArchive("DATA.ISA")
  -> FUN_10001280("DATA.ISA")
  -> FUN_10023870(&DAT_10035820, "DATA.ISA", NULL, 0)
```

**逆向意义：**

这是封包注册的公开入口，真正解析 ISA 的逻辑不在这里，而在 `FUN_10023870`。

---

### 3.3 `ISM_StartScript`

**地址 / 名称：**

```c
ISM_StartScript(char *param_1)
```

**功能：**

启动指定 ISM 脚本。

EXE 侧传入：

```text
SYSTEM.ISM
```

**逆向意义：**

脚本层逆向应从 `SYSTEM.ISM` 开始，资源定位则由 `FUN_100247a0` 这一套 VFS 负责。

---

## 4. ISA 封包注册与解析函数

### 4.1 `FUN_10001280` -> `Archive_RegisterWrapper`

**建议重命名：**

```c
FUN_10001280 -> Archive_RegisterWrapper
```

**功能：**

`ISM_RegisterArchive` 的内部包装函数。

逻辑：

```c
undefined4 FUN_10001280(char *param_1)
{
    if (param_1 != NULL) {
        return FUN_10023870(&DAT_10035820, param_1, NULL, 0);
    }
    return 0xffffffff;
}
```

**逆向意义：**

这个函数没有复杂逻辑，只是把封包名转交给主封包注册函数 `FUN_10023870`。

---

### 4.2 `FUN_10023870` -> `Isa_RegisterArchive`

**建议重命名：**

```c
FUN_10023870 -> Isa_RegisterArchive
```

**功能：**

ISA 封包注册、文件头校验、目录表读取和目录表解码的核心函数。

主要流程：

1. 初始化 archive 管理器；
2. 找一个空闲 archive slot；
3. 处理路径参数，如果传入的是目录路径，则设置搜索路径；
4. 调用 `FUN_100235e0` 打开物理封包文件；
5. 读取前 `0x10` 字节；
6. 校验签名：

```text
ISM ARCHIVED
```

7. 解析 `count_flag`；
8. 分配 `entry_count * 0x30` 大小的目录表缓冲区；
9. 读取目录表；
10. 如果 flag 指示目录表被混淆，则调用 `FUN_10023830` 解码；
11. 保存 archive 文件名、目录表指针、entry 数量等信息到 archive slot。

**已确认 ISA 头结构：**

从 `DATA.ISA` 文件头截图：

```text
49 53 4D 20 41 52 43 48 49 56 45 44 92 09 01 80
```

解析为：

```text
0x00 - 0x0B : "ISM ARCHIVED"
0x0C - 0x0F : 0x80010992，小端 uint32
```

其中：

```text
entry_count = 0x0992 = 2450
flags       = 0x8001
```

目录表大小：

```text
2450 * 0x30 = 117600 = 0x1CB60
```

目录表起点：

```text
0x10
```

数据区理论起点：

```text
0x10 + 0x1CB60 = 0x1CB70
```

**逆向意义：**

这是解包器和重封包器的核心依据。当前 `isa_archive_tool.py` 就是基于这个函数还原的格式。

---

### 4.3 `FUN_10023830` -> `Isa_DecodeIndexTable`

**建议重命名：**

```c
FUN_10023830 -> Isa_DecodeIndexTable
```

**功能：**

ISA 目录表 XOR 解码函数。

原始反编译逻辑：

```c
void FUN_10023830(int param_1, uint param_2)
{
    uint dword_count = param_2 >> 2;
    for (uint i = 0; i < dword_count; i++) {
        *(uint *)(param_1 + i * 4) ^= ~((dword_count - i) + param_2);
    }
}
```

Python 等价实现：

```python
def decode_index_table(buf: bytearray) -> None:
    table_size = len(buf)
    dword_count = table_size // 4

    for i in range(dword_count):
        pos = i * 4
        v = int.from_bytes(buf[pos:pos + 4], "little")
        key = ~((dword_count - i) + table_size) & 0xffffffff
        v ^= key
        buf[pos:pos + 4] = v.to_bytes(4, "little")
```

**已验证样例：**

`DATA.ISA` 目录表第一个 entry 开头原始字节：

```text
86 94 A9 B0 F9 EF B4 AC 8F C1 FD FF CA C1 FD FF
```

解码后：

```text
41 55 54 4F 31 2E 49 53 46 00 00 00 00 00 00 00
```

即：

```text
AUTO1.ISF
```

**逆向意义：**

这个函数是 `DATA.ISA` 目录表可读化的关键。没有它，文件名、offset、size 都会表现为乱码。

---

### 4.4 ISA 目录项结构

由 `FUN_10024140` 的访问方式可以确认目录项大小为 `0x30`。

当前结构：

```c
struct IsaEntry {
    char     name[0x24];  // 0x00 - 0x23，NUL 结尾文件名
    uint32_t offset;      // 0x24，文件偏移
    uint32_t size;        // 0x28，文件大小
    uint32_t reserved;    // 0x2C，未知 / 保留
};
```

**说明：**

- 文件名通常是 ASCII / CP932 可解码字符串；
- 引擎查找前会将请求文件名大写化；
- offset 从当前分析看应为相对于封包文件开头的绝对偏移；
- `reserved` 当前未发现必须修改，重封包时建议原样保留。

---

## 5. ISA 目录查找与虚拟文件系统函数

### 5.1 `FUN_10024140` -> `Isa_FindEntry`

**建议重命名：**

```c
FUN_10024140 -> Isa_FindEntry
```

**功能：**

在某个已注册 ISA archive 的目录表中查找指定文件名。

主要行为：

1. 遍历 archive 的目录表；
2. 每个目录项步进 `0x30` 字节；
3. 比较 entry 文件名和请求文件名；
4. 匹配后取：

```c
offset = *(uint32_t *)(entry + 0x24);
size   = *(uint32_t *)(entry + 0x28);
```

5. 返回一个内部文件句柄 / 文件记录结构。

**逆向意义：**

该函数确认了目录项结构中 `offset` 和 `size` 的偏移，是解包器解析 entry 的主要依据。

---

### 5.2 `FUN_100247a0` -> `Vfs_OpenFile`

**建议重命名：**

```c
FUN_100247a0 -> Vfs_OpenFile
```

**功能：**

ISM 引擎统一文件打开函数。脚本、资源、音频等文件最终都会通过这类 VFS 逻辑打开。

大致查找顺序：

```text
1. 规范化 / 大写化文件名
2. 查内部缓存 / 已打开记录
3. 尝试当前目录散文件
4. 尝试搜索路径散文件
5. 尝试 WAVE\ 文件
6. 遍历已注册 ISA archive，调用 FUN_10024140 查目录表
7. 遍历 plugin archive，调用 ISMPLUGIN_archive_Open
8. 全部失败则返回 -1
```

**重要结论：**

该函数表明引擎存在散文件优先逻辑。理论优先级是：

```text
散文件 > ISA 封包 > plugin archive
```

**逆向意义：**

如果后续做汉化补丁，可以优先测试同名散文件覆盖：

```text
SYSTEM.ISM
*.ISM
*.ISF
WAVE\*.wav / *.ogg
```

如果散文件覆盖有效，就不一定每次都需要重打 `DATA.ISA`。

---

### 5.3 `FUN_100235e0` -> `Vfs_OpenPhysicalFile`

**建议重命名：**

```c
FUN_100235e0 -> Vfs_OpenPhysicalFile
```

**功能：**

实际调用 WinAPI 打开物理散文件。

关键调用：

```c
CreateFileA(
    filename,
    GENERIC_READ,
    FILE_SHARE_READ,
    NULL,
    OPEN_EXISTING,
    FILE_ATTRIBUTE_NORMAL,
    NULL
)
```

失败后会继续尝试：

```text
search_path + filename
WAVE\filename
search_path + WAVE\filename
```

**逆向意义：**

这是散文件覆盖和搜索路径机制的底层依据。

---

### 5.4 `FUN_10023c30` -> `Vfs_ReadFile`

**建议重命名：**

```c
FUN_10023c30 -> Vfs_ReadFile
```

**功能：**

ISM VFS 统一读取函数。

根据打开文件来源不同，读取路径不同：

```text
source_type = 0 : 物理散文件，调用 ReadFile
source_type = 2 : ISA archive，根据 archive handle + offset + current_pos 读取
source_type = 3 : plugin archive，调用插件提供的 Read 函数
```

**逆向意义：**

若导出的 ISA 文件内容不正确，需要回到这个函数验证 offset 是绝对偏移还是数据区相对偏移。当前结合解包测试和结构判断，倾向于绝对偏移。

---

### 5.5 `FUN_10023e40` -> `Vfs_SeekFile`

**建议重命名：**

```c
FUN_10023e40 -> Vfs_SeekFile
```

**功能：**

ISM VFS 统一 seek 函数。

不同来源：

```text
物理散文件 -> SetFilePointer
ISA archive -> 修改内部 current_pos
plugin archive -> 调用插件 Lseek
```

**逆向意义：**

用于确认脚本读取是不是流式读取，以及 ISA 内部文件读指针如何维护。

---

### 5.6 `FUN_10023f90` -> `Vfs_GetFileSize`

**建议重命名：**

```c
FUN_10023f90 -> Vfs_GetFileSize
```

**功能：**

ISM VFS 统一获取文件大小函数。

不同来源：

```text
物理散文件 -> GetFileSize
ISA archive -> entry.size
plugin archive -> 插件 GetFileSize
```

**逆向意义：**

对于脚本提取工具，需要知道引擎读取整个脚本还是按块读取。这个函数可用于追踪脚本加载流程。

---

### 5.7 `FUN_10024aa0` -> `Vfs_CloseFile`

**建议重命名：**

```c
FUN_10024aa0 -> Vfs_CloseFile
```

**功能：**

关闭 VFS 文件句柄。

根据来源不同：

```text
物理散文件 -> CloseHandle
ISA archive -> 减引用 / 释放缓存
plugin archive -> 调用插件 Close
```

**逆向意义：**

通常不影响解包，但对动态调试文件读取生命周期有帮助。

---

## 6. 插件系统关键函数

### 6.1 `FUN_10025660` -> `Plugin_ScanAndLoad`

**建议重命名：**

```c
FUN_10025660 -> Plugin_ScanAndLoad
```

**功能：**

扫描并加载 `PLUGIN` 目录下的插件 DLL。

主要流程：

1. `GetModuleHandleA("ISM.DLL")`；
2. `GetModuleFileNameA` 获取 `ISM.DLL` 所在目录；
3. 拼出 `PLUGIN` 目录；
4. `SetCurrentDirectoryA(plugin_dir)`；
5. `FindFirstFileA("*.DLL")`；
6. 遍历 DLL 并 `LoadLibraryA`；
7. 检查插件导出：

```c
ISMPLUGIN_GetPluginInfo
ISMPLUGIN_GetSupportedExtensions
```

8. 根据插件类型注册到不同插件表。

**插件 archive 接口：**

如果插件类型是 archive，还会使用类似接口：

```c
ISMPLUGIN_archive_Initialize
ISMPLUGIN_archive_Finalize
ISMPLUGIN_archive_SetFileFunctions
ISMPLUGIN_archive_Register
ISMPLUGIN_archive_Unregister
ISMPLUGIN_archive_Open
ISMPLUGIN_archive_Close
ISMPLUGIN_archive_Read
ISMPLUGIN_archive_Lseek
ISMPLUGIN_archive_GetFileSize
```

**逆向意义：**

截图中的：

```text
plugin/ipng.dll
plugin/ivorbis.dll
plugin/ogg.dll
plugin/vorbis.dll
```

应该不是 EXE 直接加载，而是 ISM.DLL 的插件系统加载。资源格式扩展、OGG/Vorbis 解码、PNG 读取等逻辑可能分散在插件 DLL 中。

---

### 6.2 `FUN_10025920` -> `Plugin_InitAll`

**建议重命名：**

```c
FUN_10025920 -> Plugin_InitAll
```

**功能：**

插件系统总初始化函数。

它会调用若干插件表初始化函数，并最终调用：

```c
FUN_10025660(...)
```

完成插件扫描加载。

**逆向意义：**

如果游戏运行中缺少 `plugin` 目录，可能会导致 PNG / OGG / archive 扩展格式无法打开。

---

## 7. 推荐的 Ghidra / IDA 重命名表

| 原函数 / 全局 | 建议名 | 功能 |
|---|---|---|
| `FUN_004011c0` | `App_LoadISMAndResolveExports` | EXE 加载 ISM.DLL 并解析导出 |
| `FUN_00403cc0` | `App_MainWindowAndMessageLoop` | EXE 主窗口和启动脚本设置 |
| `FUN_00404680` | `App_RegisterHostCallbacks` | EXE 注册宿主回调给 ISM |
| `DAT_0040d984` | `pISM_RegisterArchive` | `ISM_RegisterArchive` 函数指针 |
| `DAT_0040d990` | `pISM_StartScript` | `ISM_StartScript` 函数指针 |
| `DAT_0040dfb4` | `pISM_StopScript` | `ISM_StopScript` 函数指针 |
| `DAT_0040dfb8` | `g_StartScriptName_SYSTEM_ISM` | 启动脚本名 `SYSTEM.ISM` |
| `ISM_RegisterArchive` | `ISM_RegisterArchive` | 对外封包注册接口 |
| `ISM_StartScript` | `ISM_StartScript` | 对外脚本启动接口 |
| `FUN_10001280` | `Archive_RegisterWrapper` | 封包注册包装层 |
| `FUN_10023870` | `Isa_RegisterArchive` | ISA 封包注册 / 目录读取 |
| `FUN_10023830` | `Isa_DecodeIndexTable` | ISA 目录表 XOR 解码 |
| `FUN_10024140` | `Isa_FindEntry` | ISA 目录项查找 |
| `FUN_100235e0` | `Vfs_OpenPhysicalFile` | 打开物理散文件 |
| `FUN_100247a0` | `Vfs_OpenFile` | 统一 VFS 打开函数 |
| `FUN_10023c30` | `Vfs_ReadFile` | 统一 VFS 读取函数 |
| `FUN_10023e40` | `Vfs_SeekFile` | 统一 VFS seek 函数 |
| `FUN_10023f90` | `Vfs_GetFileSize` | 统一 VFS 文件大小函数 |
| `FUN_10024aa0` | `Vfs_CloseFile` | 统一 VFS 关闭函数 |
| `FUN_10025660` | `Plugin_ScanAndLoad` | 扫描并加载 `PLUGIN/*.DLL` |
| `FUN_10025920` | `Plugin_InitAll` | 插件系统总初始化 |

---

## 8. DATA.ISA 当前格式记录

### 8.1 Header

```c
struct IsaHeader {
    char     magic[12];    // "ISM ARCHIVED"
    uint32_t count_flag;   // low16=count, high16=flags
};
```

当前样本：

```text
magic      = "ISM ARCHIVED"
count_flag = 0x80010992
count      = 0x0992 = 2450
flags      = 0x8001
```

### 8.2 Entry

```c
struct IsaEntry {
    char     name[0x24];
    uint32_t offset;
    uint32_t size;
    uint32_t reserved;
};
```

### 8.3 Index 解码条件

当前样本 `flags = 0x8001`，目录表需要 XOR 解码。

代码中判断大致等价于：

```c
if (((flags >> 8) & 0x80) != 0) {
    Isa_DecodeIndexTable(index, index_size);
}
```

### 8.4 Index 解码算法

```c
for (i = 0; i < index_size / 4; i++) {
    dword[i] ^= ~(((index_size / 4) - i) + index_size);
}
```

---

## 9. 对后续文本提取 / 注入开发的意义

### 9.1 解包阶段

优先使用 `DATA.ISA` 解包，提取出：

```text
SYSTEM.ISM
*.ISM
*.ISF
其他脚本 / 文本资源
```

当前 ISA 结构已经足够支持：

```text
unpack
list
pack
```

### 9.2 散文件覆盖测试

因为 `Vfs_OpenFile` 会先尝试物理散文件，再查 archive，所以建议测试：

```text
把修改后的 SYSTEM.ISM 放到游戏目录
或按原路径放到子目录
```

如果引擎优先读取散文件，就可以简化补丁发布结构。

### 9.3 重封包阶段

重封包必须注意：

1. 保留原始文件顺序；
2. 保留 `flags`；
3. 保留 entry 的 `reserved` 字段；
4. 如果原封包目录表加密，重封包时也需要重新 XOR 编码目录表；
5. 文件名建议维持大写或原始封包名，避免查找不一致。
