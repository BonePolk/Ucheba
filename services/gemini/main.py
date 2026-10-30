from common.http import private_app
from gemini import analyze_local, models_local

app = private_app('gemini')


@app.post('/analyze')
async def analyze(body: dict):
    return await analyze_local(**body)


@app.post('/models')
async def models(body: dict):
    return await models_local(**body)
