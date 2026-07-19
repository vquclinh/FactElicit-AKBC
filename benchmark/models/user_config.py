from enum import Enum


class Models(Enum):
    # Add your models here

    @staticmethod
    def get_model(model_name: str):
        raise ValueError(f"Model `{model_name}` not found.")
