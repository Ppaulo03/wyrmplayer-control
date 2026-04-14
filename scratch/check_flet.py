import flet as ft

def main(page: ft.Page):
    print("Attributes on page:")
    attrs = [a for a in dir(page) if "window" in a.lower() or "skip" in a.lower()]
    print(attrs)
    # Just close the view after a second
    import time
    time.sleep(1)
    # page.window_close()? No.

ft.app(target=main, view=ft.AppView.FLET_APP_HIDDEN)
