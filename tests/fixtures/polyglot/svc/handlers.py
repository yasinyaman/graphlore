from fastapi import FastAPI

from svc import store

app = FastAPI()


@app.get("/items/{item_id}")
def read_item(item_id: int):
    return store.load(item_id)
