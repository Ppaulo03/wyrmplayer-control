import flet as ft

def main(page: ft.Page):
    print("Page object attributes search for 'window':")
    print([a for a in dir(page) if "window" in a.lower()])
    print("\nPage.window object attributes:")
    if hasattr(page, "window"):
        print(dir(page.window))
    else:
        print("page.window does not exist")

ft.app(target=main, view=ft.AppView.FLET_APP_HIDDEN)
