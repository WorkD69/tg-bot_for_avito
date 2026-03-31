from aiogram.fsm.state import State, StatesGroup


class OrderEditStates(StatesGroup):
    waiting_comment = State()
    waiting_files = State()


class NoteStates(StatesGroup):
    waiting_note = State()


class MessagingStates(StatesGroup):
    waiting_message = State()


class SolutionStates(StatesGroup):
    waiting_files = State()


class ReviewStates(StatesGroup):
    waiting_rating = State()
    waiting_text = State()
