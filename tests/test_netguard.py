import pytest

from pulpie_mcp.netguard import BlockedAddressError, check_host, is_public_ip


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "::1", "fd00::1",
     "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1"],
)
def test_non_public_ips(ip):
    assert not is_public_ip(ip)


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_ips(ip):
    assert is_public_ip(ip)


@pytest.mark.anyio
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]", "169.254.169.254"])
async def test_check_host_blocks_local(host):
    with pytest.raises(BlockedAddressError, match="PULPIE_ALLOW_PRIVATE"):
        await check_host(host)


@pytest.mark.anyio
async def test_check_host_allows_public_literal():
    await check_host("1.1.1.1")


@pytest.fixture
def anyio_backend():
    return "asyncio"
