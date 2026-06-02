from aiogram.fsm.state import State, StatesGroup


class CustomServerStates(StatesGroup):
    waiting_quantity = State()
    waiting_reinstall_link = State()
    waiting_retained_ip_renewal_link = State()
    waiting_admin_expiry_time = State()
