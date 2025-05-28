from langchain_core.runnables import Runnable


class BaseRemotePregel(Runnable):
    # TODO: implement name overriding
    name: str = "LangGraph"

    # TODO: implement graph_id overriding
    graph_id: str
