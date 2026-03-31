from aiogram.fsm.state import State, StatesGroup


class OrderEditStates(StatesGroup):
    waiting_comment = State()
    waiting_files = State()


class NoteStates(StatesGroup):
    waiting_note = State()


class MessagingStates(StatesGroup):
    """Operator → client messaging."""
    waiting_message = State()


class ClientMessagingStates(StatesGroup):
    """Client → operator messaging."""
    waiting_message = State()


class SolutionStates(StatesGroup):
    waiting_files = State()


class ReviewStates(StatesGroup):
    waiting_rating = State()
    waiting_text = State()


class RequisitesStates(StatesGroup):
    """Operator sends payment requisites to client."""
    waiting_text = State()


class NegotiationStates(StatesGroup):
    """Client proposes counter price / question."""
    waiting_text = State()


class CounterOfferStates(StatesGroup):
    """Operator makes counter-offer to client."""
    waiting_amount = State()
