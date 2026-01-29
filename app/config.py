import yaml
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class InstructionSet:
    id: str
    name: str
    description: str
    instruction_file: str
    instructions: Optional[str] = None


class Config:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.instruction_sets: dict[str, InstructionSet] = {}
        self.load()

    def load(self):
        with open(self.config_path, "r") as f:
            data = yaml.safe_load(f)

        self.instruction_sets = {}
        for item in data.get("instruction_sets", []):
            instruction_set = InstructionSet(
                id=item["id"],
                name=item["name"],
                description=item["description"],
                instruction_file=item["instruction_file"]
            )
            # Load the instruction content
            instruction_path = Path("config") / item["instruction_file"]
            if instruction_path.exists():
                instruction_set.instructions = instruction_path.read_text()
            self.instruction_sets[item["id"]] = instruction_set

    def get_instruction_set(self, set_id: str) -> Optional[InstructionSet]:
        return self.instruction_sets.get(set_id)

    def get_all_instruction_sets(self) -> list[InstructionSet]:
        return list(self.instruction_sets.values())

    def reload(self):
        self.load()


# Global config instance
config = Config()
