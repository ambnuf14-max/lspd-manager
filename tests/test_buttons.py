from datetime import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from models.roles_request import DoneButton, PersistentView, DropButton, DropModal


@pytest.mark.asyncio(loop_scope="function")
@patch("models.roles_request.datetime")
async def test_done_button_callback(mock_datetime):
    fixed_time = datetime(2025, 3, 3, 1, 2, 5, 956930)
    mock_datetime.now.return_value = fixed_time

    embed = discord.Embed(title="Test Embed")
    user = MagicMock(spec=discord.User)
    user.display_name = "TestUser"
    user.id = 12345

    interaction = AsyncMock(spec=discord.Interaction)
    interaction.message = AsyncMock()
    interaction.message.id = 98765
    interaction.message.edit = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.user = user

    conn = AsyncMock()
    conn.execute = AsyncMock()

    acquire_mock = MagicMock()
    acquire_mock.__aenter__ = AsyncMock(return_value=conn)
    acquire_mock.__aexit__ = AsyncMock(return_value=None)

    db_pool = MagicMock()
    db_pool.acquire = MagicMock(return_value=acquire_mock)

    interaction.client = MagicMock()
    interaction.client.db_pool = db_pool

    view = PersistentView(embed, user)
    done_button = DoneButton(embed, user)
    view.add_item(done_button)

    await done_button.callback(interaction)

    interaction.message.edit.assert_awaited_once_with(embed=embed, view=view)
    assert embed.color == discord.Color.green()

    db_pool.acquire.assert_called_once()
    conn.execute.assert_awaited_once_with(
        "UPDATE requests SET status = 'approved', finished_by = $1, finished_at = $2 WHERE message_id = $3",
        interaction.user.id,
        fixed_time,
        interaction.message.id,
    )

    user.send.assert_awaited_once_with("Ваш запрос на получение ролей был одобрен.")


@pytest.mark.asyncio(loop_scope="function")
@patch("models.roles_request.datetime")
async def test_drop_button_callback(mock_datetime):
    fixed_time = datetime(2025, 3, 3, 1, 2, 5, 956930)
    mock_datetime.now.return_value = fixed_time

    embed = discord.Embed(title="Test Embed")
    user = MagicMock(spec=discord.User)
    user.display_name = "TestUser"
    user.id = 12345
    user.send = AsyncMock()

    mock_reason = MagicMock(spec=discord.ui.TextInput)
    mock_reason.value = "Недостаточно информации"

    interaction = AsyncMock(spec=discord.Interaction)
    interaction.message = AsyncMock()
    interaction.message.id = 98765
    interaction.message.edit = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.user = user
    interaction.client = MagicMock()
    interaction.client.db_pool = AsyncMock()

    conn = AsyncMock()
    conn.execute = AsyncMock()

    acquire_mock = MagicMock()
    acquire_mock.__aenter__ = AsyncMock(return_value=conn)
    acquire_mock.__aexit__ = AsyncMock(return_value=None)

    db_pool = MagicMock()
    db_pool.acquire = MagicMock(return_value=acquire_mock)

    interaction.client = MagicMock()
    interaction.client.db_pool = db_pool

    view = PersistentView(embed, user)
    drop_button = DropButton(embed, user)
    view.add_item(drop_button)

    await drop_button.callback(interaction)

    interaction.response.send_modal.assert_called_once()

    modal = interaction.response.send_modal.call_args[0][0]
    assert isinstance(modal, DropModal)
    assert modal.embed == embed
    assert modal.user == user
    assert modal.view == view

    modal.reason = AsyncMock()
    modal.reason.value = "Недостаточно информация"
    await modal.on_submit(interaction)

    interaction.message.edit.assert_awaited_once_with(embed=embed, view=view)
    assert embed.color == discord.Color.red()

    db_pool.acquire.assert_called_once()
    conn.execute.assert_awaited_once_with(
        "UPDATE requests SET status = 'rejected', finished_by = $1, finished_at = $2, reject_reason = $3 WHERE "
        "message_id = $4",
        interaction.user.id,
        fixed_time,
        modal.reason.value,
        interaction.message.id,
    )

    user.send.assert_awaited_once_with(
        f"Ваш запрос на получение ролей был отклонён. Причина: {modal.reason.value}"
    )

    interaction.response.send_message.assert_awaited_once_with(
        f"Запрос от {user.display_name} отклонён!", ephemeral=True
    )
