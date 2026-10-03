package ui

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/api"
	"github.com/nickpending/prismis/internal/commands"
	"github.com/nickpending/prismis/internal/db"
	"github.com/nickpending/prismis/internal/ui/operations"
)

const (
	hydrateItemID      = "item-1"
	hydrateBody        = "FULL ARTICLE BODY FROM THE DETAIL ENDPOINT"
	hydrateSummaryText = "READING SUMMARY FROM THE FULL ANALYSIS"
)

// detailServer is an httptest server that records every request and serves
// GET /api/entries/{id}?include=content for hydrateItemID (and a 500 for "bad-1").
type detailServer struct {
	*httptest.Server
	mu       sync.Mutex
	requests []string
	// analysis is the analysis JSON the detail endpoint returns for hydrateItemID
	analysis string
	// body is the content the detail endpoint returns for hydrateItemID
	body string
}

func newDetailServer(t *testing.T) *detailServer {
	t.Helper()
	ds := &detailServer{
		analysis: fmt.Sprintf(`{"kind":"news","reading_summary":%q}`, hydrateSummaryText),
		body:     hydrateBody,
	}
	ds.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ds.mu.Lock()
		ds.requests = append(ds.requests, r.Method+" "+r.URL.RequestURI())
		analysis, body := ds.analysis, ds.body
		ds.mu.Unlock()

		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/api/entries/"+hydrateItemID && r.URL.Query().Get("include") == "content":
			fmt.Fprintf(w, `{"success":true,"message":"ok","data":{"id":%q,"title":"Item One","url":"https://example.com/1","content":%q,"summary":"short","analysis":%s,"priority":"high","published_at":"2026-01-02T03:04:05+00:00","fetched_at":"2026-01-02T03:04:05+00:00"}}`, hydrateItemID, body, analysis)
		case r.URL.Path == "/api/entries":
			fmt.Fprintf(w, `{"success":true,"message":"ok","data":{"items":[{"id":%q,"title":"Item One","url":"https://example.com/1","summary":"short","analysis":{"kind":"news"},"priority":"high","published_at":"2026-01-02T03:04:05+00:00","fetched_at":"2026-01-02T03:04:05+00:00","source_name":"S"}],"total":1}}`, hydrateItemID)
		default:
			w.WriteHeader(http.StatusInternalServerError)
			fmt.Fprint(w, `{"success":false,"message":"boom"}`)
		}
	}))
	t.Cleanup(ds.Close)
	return ds
}

func (ds *detailServer) detailRequests() int {
	ds.mu.Lock()
	defer ds.mu.Unlock()
	n := 0
	for _, r := range ds.requests {
		if strings.Contains(r, "/api/entries/") {
			n++
		}
	}
	return n
}

func (ds *detailServer) allRequests() []string {
	ds.mu.Lock()
	defer ds.mu.Unlock()
	return append([]string(nil), ds.requests...)
}

// slimItem is an item as a sync merges it from a view=list response: no content,
// analysis cut to the list keys.
func slimItem(id string) db.ContentItem {
	return db.ContentItem{ID: id, Title: "Item One", Summary: "short", Priority: "high", Analysis: `{"kind":"news"}`}
}

func remoteModel(ds *detailServer, items ...db.ContentItem) Model {
	return Model{
		remoteURL:   ds.URL,
		items:       items,
		itemsCache:  append([]db.ContentItem(nil), items...),
		view:        "list",
		focusedPane: "content",
		width:       120,
		height:      50,
	}
}

func update(t *testing.T, m Model, msg tea.Msg) (Model, tea.Cmd) {
	t.Helper()
	next, cmd := m.Update(msg)
	nm, ok := next.(Model)
	if !ok {
		t.Fatalf("Update returned %T, want Model", next)
	}
	return nm, cmd
}

// recordingTools puts fake clipboard and fabric binaries first on PATH. Each writes
// its stdin to a file under the returned dir, so a test observes exactly what the
// copy and Fabric actions delivered. The fake fabric answers --listpatterns with
// "summarize" and any run with "FABRIC OUTPUT".
func recordingTools(t *testing.T) (clipboardFile, fabricFile string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("shell-script fakes need a POSIX shell")
	}
	dir := t.TempDir()
	clipboardFile = filepath.Join(dir, "clipboard.txt")
	fabricFile = filepath.Join(dir, "fabric.txt")

	clip := "#!/bin/sh\ncat > " + clipboardFile + "\n"
	for _, name := range []string{"pbcopy", "xclip", "xsel", "wl-copy"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(clip), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	fabric := "#!/bin/sh\n" +
		"if [ \"$1\" = \"--listpatterns\" ]; then echo summarize; exit 0; fi\n" +
		"cat > " + fabricFile + "\necho 'FABRIC OUTPUT'\n"
	if err := os.WriteFile(filepath.Join(dir, "fabric"), []byte(fabric), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	return clipboardFile, fabricFile
}

func readRecorded(t *testing.T, path string) (string, bool) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	return string(b), true
}

func TestHydrate_ReaderOpenFetchesOnceAndShowsSummaryAndContent(t *testing.T) {
	remoteTestConfig(t)
	ds := newDetailServer(t)
	m := remoteModel(ds, slimItem(hydrateItemID))

	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})

	if got := ds.detailRequests(); got != 1 {
		t.Fatalf("opening an unhydrated item made %d detail requests, want 1: %v", got, ds.allRequests())
	}
	if want := "GET /api/entries/" + hydrateItemID + "?include=content"; ds.allRequests()[0] != want {
		t.Errorf("request = %q, want %q", ds.allRequests()[0], want)
	}
	if m.view != "reader" {
		t.Errorf("view = %q, want reader", m.view)
	}
	if got := m.items[0].Content; got != hydrateBody {
		t.Errorf("item content = %q, want the fetched body", got)
	}
	if !strings.Contains(m.viewport.View(), hydrateSummaryText) {
		t.Errorf("reader does not show the reading summary:\n%s", m.viewport.View())
	}

	// Leave and reopen the now-hydrated item: no second request.
	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEsc})
	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})
	if got := ds.detailRequests(); got != 1 {
		t.Errorf("reopening a hydrated item made %d detail requests total, want 1", got)
	}
}

func TestHydrate_ReaderShowsFetchedContentWhenAnalysisHasNoReadingSummary(t *testing.T) {
	remoteTestConfig(t)
	ds := newDetailServer(t)
	ds.analysis = `{"kind":"news"}`
	m := remoteModel(ds, slimItem(hydrateItemID))

	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})

	if !strings.Contains(m.viewport.View(), hydrateBody) {
		t.Errorf("reader does not show the fetched content:\n%s", m.viewport.View())
	}
}

func TestHydrate_CopyContentFetchesOnceThenUsesFetchedContent(t *testing.T) {
	remoteTestConfig(t)
	clipboardFile, _ := recordingTools(t)
	ds := newDetailServer(t)
	m := remoteModel(ds, slimItem(hydrateItemID))

	m, _ = update(t, m, commands.CopyMsg{Target: "content"})

	if got := ds.detailRequests(); got != 1 {
		t.Fatalf("copy on an unhydrated item made %d detail requests, want 1", got)
	}
	if got, ok := readRecorded(t, clipboardFile); !ok || got != hydrateBody {
		t.Errorf("clipboard = %q (written=%v), want the fetched body", got, ok)
	}
	if !strings.Contains(m.statusMessage, "Content copied") {
		t.Errorf("status = %q, want a content-copied message", m.statusMessage)
	}

	// The summary target on the now-hydrated item reads the fetched analysis, with
	// no second request.
	m, _ = update(t, m, commands.CopyMsg{Target: "summary"})
	if got := ds.detailRequests(); got != 1 {
		t.Errorf("copy on a hydrated item made %d detail requests total, want 1", got)
	}
	if got, _ := readRecorded(t, clipboardFile); !strings.Contains(got, hydrateSummaryText) {
		t.Errorf("summary clipboard = %q, want the reading summary from the fetched analysis", got)
	}
	_ = m
}

func TestHydrate_FabricFetchesOnceThenSendsFetchedContent(t *testing.T) {
	remoteTestConfig(t)
	_, fabricFile := recordingTools(t)
	ds := newDetailServer(t)
	m := remoteModel(ds, slimItem(hydrateItemID))

	m, cmd := update(t, m, commands.FabricMsg{Pattern: "summarize"})
	if cmd == nil {
		t.Fatal("Fabric handler returned no command")
	}
	res, ok := cmd().(operations.FabricOperationMsg)
	if !ok || !res.Success {
		t.Fatalf("Fabric operation result = %#v, want success", res)
	}

	if got := ds.detailRequests(); got != 1 {
		t.Fatalf("Fabric on an unhydrated item made %d detail requests, want 1", got)
	}
	if got, written := readRecorded(t, fabricFile); !written || got != hydrateBody {
		t.Errorf("Fabric stdin = %q (written=%v), want the fetched body", got, written)
	}

	// Again on the hydrated item: same content, no second request.
	_, cmd = update(t, m, commands.FabricMsg{Pattern: "summarize"})
	if res, ok := cmd().(operations.FabricOperationMsg); !ok || !res.Success {
		t.Fatalf("second Fabric result = %#v, want success", res)
	}
	if got := ds.detailRequests(); got != 1 {
		t.Errorf("Fabric on a hydrated item made %d detail requests total, want 1", got)
	}
}

func TestHydrate_FailedDetailRequestShowsStatusAndLeavesItemUnhydrated(t *testing.T) {
	remoteTestConfig(t)
	clipboardFile, fabricFile := recordingTools(t)
	ds := newDetailServer(t)
	m := remoteModel(ds, slimItem("bad-1"))

	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})
	if !strings.Contains(m.statusMessage, "Failed to load content") {
		t.Errorf("reader open: status = %q, want a load-failure message", m.statusMessage)
	}

	m, _ = update(t, m, commands.FabricMsg{Pattern: "summarize"})
	if !strings.Contains(m.statusMessage, "Failed to load content") {
		t.Errorf("fabric: status = %q, want a load-failure message", m.statusMessage)
	}

	m, _ = update(t, m, commands.CopyMsg{Target: "content"})
	if !strings.Contains(m.statusMessage, "Failed to load content") {
		t.Errorf("copy: status = %q, want a load-failure message", m.statusMessage)
	}

	if _, written := readRecorded(t, clipboardFile); written {
		t.Error("the clipboard was written although the item's content could not be fetched")
	}
	if _, written := readRecorded(t, fabricFile); written {
		t.Error("Fabric received input although the item's content could not be fetched")
	}
	if _, hydrated := m.hydrated["bad-1"]; hydrated {
		t.Error("a failed fetch marked the item hydrated")
	}
	if m.items[0].Content != "" {
		t.Errorf("a failed fetch changed the item's content to %q", m.items[0].Content)
	}
	// Each action retried: unhydrated items are fetched again, not cached as failures.
	if got := ds.detailRequests(); got != 3 {
		t.Errorf("detail requests = %d, want 3 (one per action)", got)
	}
}

func TestHydrate_SyncRemergeMakesItemUnhydratedAgain(t *testing.T) {
	remoteTestConfig(t)
	ds := newDetailServer(t)
	m := remoteModel(ds, slimItem(hydrateItemID))
	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})
	if got := ds.detailRequests(); got != 1 {
		t.Fatalf("setup: %d detail requests, want 1", got)
	}

	// A real sync: fetchItemsRemote asks for view=list and re-merges the item slim.
	loaded := fetchItemsRemote(m)
	if loaded.err != nil {
		t.Fatalf("fetchItemsRemote: %v", loaded.err)
	}
	var listRequest string
	for _, r := range ds.allRequests() {
		if strings.Contains(r, "/api/entries?") {
			listRequest = r
		}
	}
	if !strings.Contains(listRequest, "view=list") {
		t.Fatalf("sync request %q does not ask for view=list", listRequest)
	}
	if len(loaded.mergedIDs) != 1 || loaded.mergedIDs[0] != hydrateItemID {
		t.Fatalf("mergedIDs = %v, want [%s]", loaded.mergedIDs, hydrateItemID)
	}
	m, _ = update(t, m, loaded)
	if m.items[0].Content != "" {
		t.Errorf("a re-merged item kept content %q, want it slim again", m.items[0].Content)
	}

	ds.mu.Lock()
	ds.body = "UPDATED BODY"
	ds.mu.Unlock()
	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEsc})
	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})
	if got := ds.detailRequests(); got != 2 {
		t.Errorf("opening a re-merged item made %d detail requests total, want 2", got)
	}
	if m.items[0].Content != "UPDATED BODY" {
		t.Errorf("content after re-open = %q, want the refetched body", m.items[0].Content)
	}
}

func TestHydrate_SyncOfOtherItemsKeepsHydratedContent(t *testing.T) {
	remoteTestConfig(t)
	ds := newDetailServer(t)
	m := remoteModel(ds, slimItem(hydrateItemID), slimItem("other"))
	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})

	// A sync that re-merged only "other", built from a cache snapshot taken before
	// item-1 was hydrated, must not hand item-1 back slim.
	snapshot := []db.ContentItem{slimItem(hydrateItemID), slimItem("other")}
	m, _ = update(t, m, itemsLoadedMsg{
		items:       append([]db.ContentItem(nil), snapshot...),
		allItems:    snapshot,
		updateCache: true,
		mergedIDs:   []string{"other"},
	})

	if m.items[0].Content != hydrateBody {
		t.Errorf("hydrated item content after an unrelated sync = %q, want the fetched body", m.items[0].Content)
	}
	if m.itemsCache[0].Content != hydrateBody {
		t.Errorf("cached hydrated item content = %q, want the fetched body", m.itemsCache[0].Content)
	}
	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEsc})
	_, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})
	if got := ds.detailRequests(); got != 1 {
		t.Errorf("detail requests = %d, want 1", got)
	}
}

func TestHydrate_LocalModeMakesNoAPIRequest(t *testing.T) {
	remoteTestConfig(t)
	clipboardFile, fabricFile := recordingTools(t)
	ds := newDetailServer(t)
	// If local mode wrongly built an API client, it would resolve to this server.
	api.SetRemoteURL(ds.URL)
	t.Cleanup(func() { api.SetRemoteURL("") })

	full := slimItem(hydrateItemID)
	full.Content = "LOCAL FULL CONTENT"
	m := Model{
		items:       []db.ContentItem{full},
		view:        "list",
		focusedPane: "content",
		width:       120,
		height:      50,
	}

	m, _ = update(t, m, tea.KeyMsg{Type: tea.KeyEnter})
	m, _ = update(t, m, commands.CopyMsg{Target: "content"})
	_, cmd := update(t, m, commands.FabricMsg{Pattern: "summarize"})
	if res, ok := cmd().(operations.FabricOperationMsg); !ok || !res.Success {
		t.Fatalf("local Fabric result = %#v, want success", res)
	}

	if reqs := ds.allRequests(); len(reqs) != 0 {
		t.Errorf("local mode made API requests: %v", reqs)
	}
	if got, _ := readRecorded(t, clipboardFile); got != "LOCAL FULL CONTENT" {
		t.Errorf("clipboard = %q, want the local row's content", got)
	}
	if got, _ := readRecorded(t, fabricFile); got != "LOCAL FULL CONTENT" {
		t.Errorf("Fabric stdin = %q, want the local row's content", got)
	}
}
