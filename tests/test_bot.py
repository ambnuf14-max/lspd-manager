import pytest
from bot.bot import CustomBot


@pytest.fixture
def bot():
    return CustomBot()


def test_bot_initialization(bot):
    assert bot.user is None
    assert bot.is_ready() is False
