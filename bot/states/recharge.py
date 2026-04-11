from aiogram.fsm.state import State, StatesGroup


class RechargeStates(StatesGroup):
    waiting_amount = State()
