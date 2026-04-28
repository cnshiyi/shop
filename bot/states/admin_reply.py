from aiogram.fsm.state import State, StatesGroup


class AdminReplyStates(StatesGroup):
    waiting_reply = State()
