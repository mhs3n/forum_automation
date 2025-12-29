import asyncio
from computer import Computer

async def main():
    computer = Computer(os_type="windows", use_host_computer_server=True, api_host="127.0.0.1", api_port=8000)
    await computer.run()
    try:
        shot = await computer.interface.screenshot()
        print("screenshot bytes:", len(shot))
        await computer.interface.left_click(100, 100)
        await computer.interface.type_text("Hello from CUA test")
    finally:
        await computer.disconnect()

asyncio.run(main())
