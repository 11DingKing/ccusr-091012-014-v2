# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

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

## 领用包（整套领用）

办案人员按固定组合（主机、配件、封存介质）整套领用，避免逐件申请漏项：

- `POST /api/kits/` 创建领用包（可携带首个版本的物资项），`GET /api/kits/` 列表
- `POST /api/kits/{id}/versions/` 包定义升级（新建版本，缺省复制当前已发布版本）
- `PUT /api/kit-versions/{id}/` 修改草稿版本物资项（已发布版本不可修改）
- `POST /api/kit-versions/{id}/publish/` 发布版本（旧版本自动停用，历史申请仍锁定原版本）
- `POST /api/kit-applications/` 创建领用申请：按当前已发布版本展开为具体物资占用，
  必需项全部可用才进入审批（`pending`），否则整单落为缺货待补（`shortage`）并返回缺货明细；
  `substitute_choices` 选择可替代物资时必须填写 `reason` 选择依据
- `POST /api/kit-applications/{id}/approve|reject|cancel|issue|return|recheck/`
  审批、拒绝、取消、发放、退回（支持部分退回）、缺货重新校验
- `POST /api/goods/{id}/freeze|unfreeze/` 冻结/解冻货物，冻结货物不可被占用或发放

所有库存数量变更均通过带守卫条件的原子更新完成：部分退回、包定义升级、
货物冻结、两个申请并发争用同一配件时，数量与包状态保持一致。

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
