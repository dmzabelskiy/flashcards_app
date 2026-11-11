from django.contrib import messages
from django.shortcuts import render

from core.language_modes import get_language_mode
from imports_app.forms import CsvImportForm
from imports_app.services import import_cards_from_csv


def import_cards(request):
    result = None
    language_mode = get_language_mode(request)
    if request.method == "POST":
        form = CsvImportForm(
            request.POST, request.FILES, language_mode=language_mode, user=request.user
        )
        if form.is_valid():
            csv_text = form.cleaned_data["csv_file"].read().decode("utf-8-sig", errors="replace")
            result = import_cards_from_csv(form.cleaned_data["deck"], csv_text)
            if result.imported_count:
                messages.success(request, f"Imported {result.imported_count} card(s).")
            if result.errors:
                messages.warning(request, f"{len(result.errors)} row(s) had errors.")
    else:
        form = CsvImportForm(language_mode=language_mode, user=request.user)
    return render(request, "imports_app/import.html", {"form": form, "result": result})
