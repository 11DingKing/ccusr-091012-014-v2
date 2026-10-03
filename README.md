# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 领用包（固定组合领用）

针对办案人员「主机 + 配件 + 封存介质」的固定组合领用需求，支持版本化领用包定义与整套占用：

- **版本化定义**：`/api/kits/` 维护领用包；每个包可创建多个版本（草稿/生效/归档），生效版本不可变，调整内容需升级新版本，旧申请始终引用其提交时的版本。
- **固定项与可替代项**：版本明细包含必需/可选固定项，以及「可替代项分组」（如多种型号封存介质任选其一）；提交申请时必须为替代项记录**选择依据**。
- **整套校验与占用**：`/api/kit-requests/preview/` 先校验整套是否可交付；`/api/kit-requests/` 提交时在单事务内展开为具体物资并占用库存，任一必需项不足则整单拒绝、不留占用。
- **审批 → 发放 → 退回**：驳回/撤回自动释放占用；批准后发放核减库存并生成出库记录；支持部分退回（行级数量校验）与全部退回（生成入库记录），包状态随行状态一致流转。
- **冻结**：`/api/stock-freeze/freeze|unfreeze/` 冻结自由库存，冻结部分不可被新申请占用，冻结/解冻均留痕。
- **并发安全**：所有数量变动使用 `UPDATE ... WHERE 数量足够` 的条件更新落账，两个申请并发争用同一配件时只有一方成功，绝不超占；SQLite 引擎以 `BEGIN IMMEDIATE` 启动写事务并对锁冲突退避重试。

主要接口：`/api/kits/`、`/api/kits/{id}/versions/`、`/api/kit-versions/{id}/activate/`、`/api/kit-requests/preview/`、`/api/kit-requests/`、`/api/kit-requests/{id}/approve|cancel|issue|return/`、`/api/stock-freeze/{freeze|unfreeze}/`。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

## 测试

```bash
cd backend
pytest -q
```

## 编译检查

```bash
python -m compileall -q backend
```

## API 验收

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py shell -c "from rest_framework.test import APIClient; from apps.authentication.models import User; u=User.objects.create_user('smoke','safe-pass',role='admin'); c=APIClient(); r=c.post('/api/auth/login/',{'username':'smoke','password':'safe-pass'},format='json'); print(r.status_code, bool(r.json()['data']['token']))"
```

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```
