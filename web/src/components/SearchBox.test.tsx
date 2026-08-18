import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { SearchBox } from "./SearchBox";

afterEach(() => vi.restoreAllMocks());

describe("SearchBox", () => {
  it("submits the typed query", async () => {
    const onSubmit = vi.fn();
    render(<SearchBox initial="" onSubmit={onSubmit} />);
    await userEvent.type(screen.getByRole("searchbox"), "power supply{enter}");
    expect(onSubmit).toHaveBeenCalledWith("power supply");
  });

  it("accepts Thaana input and marks the field rtl as it is typed", async () => {
    render(<SearchBox initial="" onSubmit={() => {}} />);
    const box = screen.getByRole("searchbox");
    await userEvent.type(box, "ވަޒީފާ");
    expect(box).toHaveAttribute("dir", "rtl");
  });

  it("stays ltr for Latin input", async () => {
    render(<SearchBox initial="" onSubmit={() => {}} />);
    const box = screen.getByRole("searchbox");
    await userEvent.type(box, "phone");
    expect(box).toHaveAttribute("dir", "ltr");
  });

  it("shows suggestions after a pause and not on every keystroke", async () => {
    const spy = vi.spyOn(api, "getSuggest").mockResolvedValue({
      suggestions: [{ term: "iphone", doc_type: "shopping" }],
    });
    render(<SearchBox initial="" onSubmit={() => {}} />);
    // Typing four characters takes well under the 200ms debounce, so the
    // suggest call must not have fired yet.
    await userEvent.type(screen.getByRole("searchbox"), "ipho");
    expect(spy).not.toHaveBeenCalled();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  });

  it("does not query suggest for a single character", async () => {
    const spy = vi.spyOn(api, "getSuggest");
    render(<SearchBox initial="" onSubmit={() => {}} />);
    await userEvent.type(screen.getByRole("searchbox"), "i");
    expect(spy).not.toHaveBeenCalled();
  });
});
