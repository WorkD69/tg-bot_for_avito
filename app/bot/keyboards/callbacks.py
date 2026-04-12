from aiogram.filters.callback_data import CallbackData


class OrderCB(CallbackData, prefix="order"):
    order_id: int
    action: str
    # action values:
    #   view            — open order card in operator DM (from group or list)
    #   bid             — start bid FSM (operator)
    #   files           — show files view (edit message)
    #   back            — return to order card from files view
    #   msg             — start messaging FSM (operator → client)
    #   client_msg      — start messaging FSM (client → operator)
    #   note            — start note FSM (operator)
    #   solution        — start solution upload FSM (operator only)
    #   client_solution — view solution files (client only; kept separate to avoid collision with operator handler)
    #   review          — start review FSM (client)
    #   client_view     — open order card in client DM (separate action to avoid operator handler)
    #   back_list       — go back to active orders list (client)
    #   back_history    — go back to history list (client)
    #   cancel          — cancel order prompt (client)
    #   cancel_yes      — confirm cancel (client)
    #   cancel_no       — abort cancel (client)
    #   add_comment     — add comment to order (client)
    #   add_files       — add files to order (client)
    #   pay             — show payment link (client, awaiting_payment)
    #   negotiate       — start price negotiation (client, awaiting_payment)
    #   send_req        — operator sends payment requisites to client
    #   negot_accept    — operator accepts client counter-offer
    #   negot_counter   — operator makes counter-offer
    #   negot_cancel    — operator cancels order from negotiation
    #   complete        — admin/operator marks order complete
    #   refresh         — operator refreshes order card in-place (edit_message_text)
    #   client_refresh  — client refreshes order card in-place (edit_message_text)
    #   new_order       — start new order FSM from completed order card (client)


class ReviewCB(CallbackData, prefix="review"):
    review_id: int
    action: str
    # action values: approve | reject


class ReviewListCB(CallbackData, prefix="reviews"):
    action: str
    page: int = 0
    # action values: all | mine


class RatingCB(CallbackData, prefix="rating"):
    order_id: int
    stars: int  # 1..5


class NegotCB(CallbackData, prefix="negot"):
    """Operator response to client price negotiation.

    proposed_amount: decimal string of client's proposed price (empty = no price proposal).
    Only meaningful for action="accept" — encodes which amount the operator is accepting.

    revision: payment_revision of the order at the time this message was sent.
    Checked on Accept/Counter to detect stale callbacks (client sent multiple counter-offers).
    If order.payment_revision != revision → this negotiation round is outdated → reject.
    """
    order_id: int
    action: str
    proposed_amount: str = ""
    revision: int = 0
    # action values: accept | counter | cancel
