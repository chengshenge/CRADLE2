from gradio_client import Client

urls = [
    "http://127.0.0.1:8080/",
    "http://127.0.0.1:8081/",
    "http://127.0.0.1:8082/",
]

for u in urls:
    print("\n===", u, "===")
    c = Client(u)
    # 关键：能不能拿到 API 描述
    api = c.view_api()
    print(api)
