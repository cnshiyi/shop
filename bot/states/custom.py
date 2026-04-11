from aiogram.fsm.state import State, StatesGroup


class CustomServerStates(StatesGroup):
    waiting_port = State()
