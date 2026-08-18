import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { jobResult, newsResult, shoppingResult } from "@/test/fixtures";
import { ResultList } from "./ResultList";

afterEach(() => vi.restoreAllMocks());

describe("ResultList", () => {
  it("dispatches each result to the card for its type", () => {
    render(<ResultList results={[jobResult, shoppingResult, newsResult]}
                       queryId={1} />);
    expect(screen.getByText("Administrative Officer")).toBeInTheDocument();
    expect(screen.getByText("MVR 850")).toBeInTheDocument();
    expect(screen.getByText(/harbour works/i)).toBeInTheDocument();
  });

  it("logs a click with its zero-based position", async () => {
    const spy = vi.spyOn(api, "postClick").mockResolvedValue(undefined);
    render(<ResultList results={[jobResult, shoppingResult]} queryId={42} />);
    await userEvent.click(screen.getByText("MVR 850"));
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ query_id: 42, document_id: shoppingResult.id,
                                position: 1 })
    );
  });

  it("does not log when there is no query id", async () => {
    const spy = vi.spyOn(api, "postClick").mockResolvedValue(undefined);
    render(<ResultList results={[jobResult]} queryId={null} />);
    await userEvent.click(screen.getByText("Administrative Officer"));
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders an empty state rather than an empty page", () => {
    render(<ResultList results={[]} queryId={1} />);
    expect(screen.getByText(/no results/i)).toBeInTheDocument();
  });

  it("uses a grid for a shopping-only result set and a list otherwise", () => {
    const { rerender } = render(
      <ResultList results={[shoppingResult]} queryId={1} tab="shopping" />
    );
    expect(screen.getByTestId("results")).toHaveClass("grid");
    rerender(<ResultList results={[jobResult]} queryId={1} tab="job" />);
    expect(screen.getByTestId("results")).not.toHaveClass("grid");
  });
});
