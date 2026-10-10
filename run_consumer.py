import uvicorn


if __name__ == "__main__":
    uvicorn.run("forge.consumer_app:app", host="0.0.0.0", port=8010, reload=False)
