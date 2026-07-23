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
