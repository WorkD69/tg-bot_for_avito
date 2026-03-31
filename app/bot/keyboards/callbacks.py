from aiogram.filters.callback_data import CallbackData


class OrderCB(CallbackData, prefix="order"):
    order_id: int
    action: str
    # action values:
    #   view        — open order card in operator DM
    #   bid         — start bid FSM
    #   files       — show files view (edit message)
    #   back        — return to order card from files view
    #   msg         — start messaging FSM (operator → client)
    #   note        — start note FSM
    #   solution    — start solution upload FSM
    #   review      — start review FSM (client)


class ReviewCB(CallbackData, prefix="review"):
    review_id: int
    action: str
    # action values: approve | reject


class ReviewListCB(CallbackData, prefix="reviews"):
    action: str
    # action values: all | mine


class RatingCB(CallbackData, prefix="rating"):
    order_id: int
    stars: int  # 1..5
