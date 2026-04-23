from aiogram.fsm.state import State, StatesGroup


class CloudQueryStates(StatesGroup):
    waiting_ip = State()
