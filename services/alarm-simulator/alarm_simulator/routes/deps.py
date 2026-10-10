from fastapi import Request

from alarm_simulator.data import DataStore


def get_store(request: Request) -> DataStore:
    store: DataStore = request.app.state.store
    return store
