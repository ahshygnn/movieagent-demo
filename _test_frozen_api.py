"""一次性 E2E：POST /api/generate + 轮询 GET /api/status/{task_id}。不写密钥到 stdout。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
PAYLOAD = {
    "script_synopsis": "Anna, Elsa, Kristoff, Olaf, and Mattias embark on a journey to uncover the truth behind the mysterious voice calling Elsa. As they travel to the enchanted forest, they discover that the past holds secrets about their kingdom and Elsa's powers. Mattias, a loyal Arendelle soldier trapped in the forest for years, helps them navigate the tensions between Arendelle and the Northuldra people. As Elsa ventures deeper, she learns that she is the key to restoring balance. Meanwhile, Anna faces her own challenges, proving her courage and leadership. In the end, the sisters embrace their destinies—Elsa chooses to protect the enchanted forest, while Anna becomes the new queen of Arendelle, ensuring peace for both lands.",
    "characters": ["Anna", "Elsa", "Kristoff", "Mattias", "Olaf"],
}


def http_json(method: str, path: str, body=None, timeout=120):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    r = http_json("POST", "/api/generate", PAYLOAD)
    task_id = r["task_id"]
    print("task_id:", task_id)

    out_path = "_last_status.json"
    last = None
    for i in range(240):  # 最多约 20 分钟（每 5s）
        last = http_json("GET", f"/api/status/{task_id}")
        st = last.get("status")
        prog = last.get("progress", 0)
        logs = last.get("logs") or []
        tail = logs[-2:] if len(logs) >= 2 else logs
        # Windows 控制台常为 GBK，日志含 emoji 会 UnicodeEncodeError
        print(f"[{i}] status={st} progress={prog} logs_tail={tail!r}")
        if st in ("done", "error"):
            break
        time.sleep(5)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(last, f, ensure_ascii=False, indent=2)
    print("wrote full last payload to", out_path)


if __name__ == "__main__":
    main()
