from aiogram.fsm.state import State, StatesGroup


class OrderCreateStates(StatesGroup):
    waiting_files = State()
    waiting_comment = State()
    waiting_deadline = State()
    waiting_budget = State()
    waiting_promo = State()
