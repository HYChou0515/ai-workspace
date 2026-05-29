"""The package's exported commands. The CLI's dispatcher iterates this
dict to wire up the 3-stage contract; adding a new command is one entry
+ one module."""

from . import plot, summarise

COMMANDS = {
    "summarise": summarise,
    "plot": plot,
}
