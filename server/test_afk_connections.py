"""Exercise AFK and incomplete-frame policy through the actual TCP handler."""
import asyncio
import json

import pytest

import db
import server


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(server, 'HANDSHAKE_SECONDS', 0.1)
    monkeypatch.setattr(server, 'FRAME_SECONDS', 0.08)
    # Make the former three-minute policy fail quickly when run against old code.
    monkeypatch.setattr(server, 'IDLE_SECONDS', 0.04, raising=False)

    def forbidden_db():
        raise AssertionError('Transport-only test opened a database')

    monkeypatch.setattr(db, 'connect', forbidden_db)

    async def authenticate(session, writer, raw):
        message = json.loads(raw)
        if message['Cmd'] == 'Login':
            session.char = {'id': 1}
            writer.write(b'logged-in\0')
        elif session.char is not None:
            writer.write(b'pong\0')
        await writer.drain()

    monkeypatch.setattr(server, 'dispatch', authenticate)


async def with_connection(check):
    before = dict(server._connections)
    closed = asyncio.Event()

    async def accept(reader, writer):
        try:
            await server.handle_client(reader, writer)
        finally:
            closed.set()

    listener = await asyncio.start_server(accept, '127.0.0.1', 0)
    reader, writer = await asyncio.open_connection('127.0.0.1', listener.sockets[0].getsockname()[1])
    try:
        await check(reader, writer)
    finally:
        writer.close()
        await writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await asyncio.wait_for(closed.wait(), 1)
    assert server._connections == before


async def login(reader, writer):
    writer.write(b'{"Cmd":"Login"}\0')
    await writer.drain()
    assert await asyncio.wait_for(reader.readuntil(b'\0'), 1) == b'logged-in\0'


def test_authenticated_afk_socket_stays_open_and_resumes(transport):
    async def check(reader, writer):
        await login(reader, writer)
        # Remain silent past both scaled login and old idle deadlines.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), 0.2)
        writer.write(b'{"Cmd":"Ping"}\0')
        await writer.drain()
        assert await asyncio.wait_for(reader.readuntil(b'\0'), 1) == b'pong\0'

    asyncio.run(with_connection(check))


def test_authenticated_partial_frame_still_times_out(transport):
    async def check(reader, writer):
        await login(reader, writer)
        writer.write(b'{"Cmd":')
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 1) == b''

    asyncio.run(with_connection(check))


def test_unauthenticated_afk_socket_still_times_out(transport):
    async def check(reader, writer):
        assert await asyncio.wait_for(reader.read(), 1) == b''

    asyncio.run(with_connection(check))
