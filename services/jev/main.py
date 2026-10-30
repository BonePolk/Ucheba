from common.http import private_app
from jev import evaluate_local

app = private_app('jev')


@app.post('/evaluate')
async def evaluate(body: dict):
    return await evaluate_local(**body)
