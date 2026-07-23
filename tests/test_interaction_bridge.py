import asyncio
from types import SimpleNamespace

from qqofficial_hub import interaction_bridge


class Receiver:
    async def callback(self, client, interaction):
        return 0


def test_interaction_bridge_acknowledges_first_delivery_once():
    async def scenario():
        state = interaction_bridge._state()
        state.update({"owner": "test", "callback": None, "seen": __import__("collections").OrderedDict(), "inflight": set(), "lock": None})
        receiver = Receiver()
        import weakref
        state["callback"] = weakref.WeakMethod(receiver.callback)
        calls = []

        async def ack(interaction_id, code):
            calls.append((interaction_id, code))

        client = SimpleNamespace(api=SimpleNamespace(on_interaction_result=ack))
        interaction = SimpleNamespace(id="same", type=11)
        await interaction_bridge._dispatch(client, interaction)
        await interaction_bridge._dispatch(client, interaction)
        assert calls == [("same", 0)]
    asyncio.run(scenario())


def test_ack_failure_retries_ack_without_reexecuting_callback():
    async def scenario():
        state = interaction_bridge._state()
        state.update({"owner": "test", "callback": None, "seen": __import__("collections").OrderedDict(), "inflight": set(), "lock": None})
        callback_calls = []

        class OnceReceiver:
            async def callback(self, client, interaction):
                callback_calls.append(interaction.id)
                return 0

        receiver = OnceReceiver()
        import weakref
        state["callback"] = weakref.WeakMethod(receiver.callback)
        ack_calls = []

        async def ack(interaction_id, code):
            ack_calls.append((interaction_id, code))
            if len(ack_calls) == 1:
                raise RuntimeError("temporary ack failure")

        client = SimpleNamespace(api=SimpleNamespace(on_interaction_result=ack))
        interaction = SimpleNamespace(id="retry-ack", type=11)
        await interaction_bridge._dispatch(client, interaction)
        await interaction_bridge._dispatch(client, interaction)
        assert callback_calls == ["retry-ack"]
        assert ack_calls == [("retry-ack", 0), ("retry-ack", 0)]
    asyncio.run(scenario())
