# MovieAgent Live Frontend

这是 `movieagent-showcase-frontend` 的独立副本，用于连接 MovieAgent FastAPI 后端。静态展示版不会随本目录的开发而改变。

## 本地启动

先在项目根目录启动后端：

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

再启动本前端：

```powershell
cd movieagent-live-frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5175
```

打开 `http://127.0.0.1:5175/`。Vite 会将 `/api` 和 `/outputs` 请求代理到 `http://127.0.0.1:8765`。

## 已连接流程

- 输入故事与角色，创建真实规划任务
- 轮询并展示 Director、Scene、Shot Agent 的进度和日志
- 展示真实分场、分镜和 Token 消耗
- 为选中镜头生成关键帧、视频和音频
- 将已有镜头视频拼接为最终成片
- 保留原静态案例和成片展示入口
