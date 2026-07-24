from typing import Any


def to_dict(model: Any):
    """
    Convert Pydantic v1/v2 models into plain dictionaries.
    """

    if hasattr(model, "model_dump"):
        return model.model_dump()

    if hasattr(model, "dict"):
        return model.dict()

    return model