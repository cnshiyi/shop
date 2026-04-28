from aiogram.fsm.state import State, StatesGroup


class MonitorStates(StatesGroup):
    waiting_address = State()
    waiting_remark = State()
    waiting_usdt_threshold = State()
    waiting_trx_threshold = State()
    waiting_energy_threshold = State()
    waiting_bandwidth_threshold = State()
