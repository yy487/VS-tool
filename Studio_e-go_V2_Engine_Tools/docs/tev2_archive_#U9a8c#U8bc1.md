# TE_V2 资源包验证

当前资源包正式验证入口：

```powershell
python .\regression_test.py
```

当前已覆盖：

- 资源包探针清单生成
- `gameXX.dat` 样本存在性和数量检查
- 单个 `PAK0` 正式解出
- 解出结果按 `group_name/file_name` 落位

当前未覆盖：

- 正式 unpack -> pack 回环
- 字节级一致性验证
