
from uuid import UUID
from pydantic import BaseModel, RootModel
from typing import (
    Deque, Dict, FrozenSet, List, Literal, Optional, Sequence, Set, Tuple, Union
)


class Path(BaseModel):
   filename:str

class AnnotationId(RootModel):
   root: UUID


class BaseAnnotation(BaseModel):
   id:str
   reviewed:bool
   visited:bool


class CentroidAnnotation(BaseAnnotation):
   type: Literal['point']
   anntype: Literal['neuron','glia']
   point: Tuple[int,int,int]


class CellAnnotation(BaseAnnotation):
   type: Literal['polygon']
   anntype: Literal['neuron','glia']
   points: List[Tuple[int,int,int]]


class FiberAnnotation(BaseAnnotation):
   type: Literal['linestring']
   anntype: Literal['axon']
   points: List[Tuple[int,int,int]]
   length: float
