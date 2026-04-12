from aiogram.fsm.state import State, StatesGroup


class CustomServerStates(StatesGroup):
    waiting_quantity = State()
    waiting_port = State()
