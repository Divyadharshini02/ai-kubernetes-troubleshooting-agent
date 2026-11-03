import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { chatAnswer, fakeFetch, message } from '../test/fixtures.js'
import ChatPanel from './ChatPanel.jsx'

const CHAT = '/api/investigations/inv-1/chat'

let fake

function setup(routes) {
  fake = fakeFetch(routes)
  vi.stubGlobal('fetch', fake.fetch)
  render(<ChatPanel investigationId="inv-1" />)
}

function input() {
  return screen.getByLabelText('Your question')
}

function sendButton() {
  return screen.getByRole('button', { name: /Send|Waiting/ })
}

function type(text) {
  fireEvent.change(input(), { target: { value: text } })
}

function exchange(kind, text, extra = {}) {
  return { question: message(1, 'user', 'question', 'My question'), reply: message(2, 'assistant', kind, text, extra) }
}

beforeEach(() => vi.unstubAllGlobals())
afterEach(cleanup)

describe('ChatPanel history', () => {
  it('loads and shows saved messages, user and assistant distinctly, with times', async () => {
    setup({
      [`GET ${CHAT}`]: () => [
        200,
        [message(1, 'user', 'question', 'Why is it failing?'), message(2, 'assistant', 'answer', 'It exits.', { answer: chatAnswer() })],
      ],
    })

    expect(await screen.findByText('Why is it failing?')).toBeTruthy()
    expect(screen.getByText('It exits.')).toBeTruthy()
    expect(screen.getByText('You')).toBeTruthy()
    expect(screen.getByText('AI answer')).toBeTruthy()
    expect(screen.getByText('About this investigation')).toBeTruthy()
    expect(document.querySelectorAll('time')).toHaveLength(2)
    expect(document.querySelector('.chat-message.user')).toBeTruthy()
    expect(document.querySelector('.chat-message.assistant')).toBeTruthy()
  })

  it('shows an empty state', async () => {
    setup({ [`GET ${CHAT}`]: () => [200, []] })

    expect(await screen.findByText(/No messages yet/)).toBeTruthy()
  })

  it('shows a load failure and retries only when asked', async () => {
    let attempts = 0
    setup({
      [`GET ${CHAT}`]: () => {
        attempts += 1
        return attempts === 1 ? [503, { detail: { code: 'storage_unavailable', message: 'History unavailable.' } }] : [200, []]
      },
    })

    expect(await screen.findByText(/could not be loaded: History unavailable/)).toBeTruthy()
    expect(attempts).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No messages yet/)).toBeTruthy()
    expect(attempts).toBe(2)
  })

  it('labels general guidance as not about this cluster', async () => {
    setup({
      [`GET ${CHAT}`]: () => [200, [message(2, 'assistant', 'answer', 'Probes check health.', { answer: chatAnswer({ scope: 'general', evidence_ids: [] }) })]],
    })

    expect(await screen.findByText("General guidance, not based on this cluster's evidence")).toBeTruthy()
  })
})

describe('ChatPanel sending', () => {
  it('sends the message and shows the reply', async () => {
    setup({
      [`GET ${CHAT}`]: () => [200, []],
      [`POST ${CHAT}`]: () => [200, exchange('answer', 'The process exits.', { answer: chatAnswer() })],
    })
    await screen.findByText(/No messages yet/)

    type('Why is the pod failing?')
    fireEvent.click(sendButton())

    expect(await screen.findByText('The process exits.')).toBeTruthy()
    const post = fake.calls.find((call) => call.method === 'POST')
    expect(JSON.parse(post.body)).toEqual({ message: 'Why is the pod failing?' })
    expect(input().value).toBe('')
  })

  it('sends with Enter, but Shift+Enter does not send', async () => {
    setup({ [`GET ${CHAT}`]: () => [200, []], [`POST ${CHAT}`]: () => [200, exchange('answer', 'Done.', { answer: chatAnswer() })] })
    await screen.findByText(/No messages yet/)
    type('Why?')

    fireEvent.keyDown(input(), { key: 'Enter', shiftKey: true })
    expect(fake.calls.filter((call) => call.method === 'POST')).toHaveLength(0)

    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(await screen.findByText('Done.')).toBeTruthy()
    expect(fake.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })

  it('disables sending while a reply is pending, and sends only once', async () => {
    let release
    setup({
      [`GET ${CHAT}`]: () => [200, []],
      [`POST ${CHAT}`]: () => new Promise((resolve) => (release = () => resolve([200, exchange('answer', 'Later.', { answer: chatAnswer() })]))),
    })
    await screen.findByText(/No messages yet/)
    type('Why?')

    fireEvent.click(sendButton())

    await waitFor(() => expect(sendButton().disabled).toBe(true))
    expect(input().disabled).toBe(true)
    expect(screen.getByRole('status').textContent).toMatch(/Thinking/)
    fireEvent.keyDown(input(), { key: 'Enter' })
    fireEvent.click(sendButton())
    expect(fake.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
    release()
    expect(await screen.findByText('Later.')).toBeTruthy()
    expect(input().disabled).toBe(false)
  })

  it('limits input to the backend maximum and shows a counter', async () => {
    setup({ [`GET ${CHAT}`]: () => [200, []] })
    await screen.findByText(/No messages yet/)

    expect(input().getAttribute('maxLength')).toBe('2000')
    type('abc')
    expect(screen.getByText('3 / 2000')).toBeTruthy()
  })

  it('does not send an empty or whitespace message', async () => {
    setup({ [`GET ${CHAT}`]: () => [200, []] })
    await screen.findByText(/No messages yet/)

    type('   ')

    expect(sendButton().disabled).toBe(true)
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(fake.calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })

  it('fills the input from a follow-up question without sending it', async () => {
    setup({
      [`GET ${CHAT}`]: () => [200, [message(2, 'assistant', 'answer', 'It exits.', { answer: chatAnswer() })]],
    })

    fireEvent.click(await screen.findByRole('button', { name: 'What do the previous logs show?' }))

    expect(input().value).toBe('What do the previous logs show?')
    expect(fake.calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })

  it.each([
    ['off_topic', 'I can help with Kubernetes troubleshooting.'],
    ['clarify', 'Could you say a little more?'],
    ['policy', "I can't change how I work."],
  ])('shows a %s reply from the topic guard, marked as made without the AI', async (kind, text) => {
    setup({ [`GET ${CHAT}`]: () => [200, []], [`POST ${CHAT}`]: () => [200, exchange(kind, text)] })
    await screen.findByText(/No messages yet/)
    type('Shall we go for lunch?')

    fireEvent.click(sendButton())

    expect(await screen.findByText(text)).toBeTruthy()
    expect(screen.getByText('Topic guard: no AI request was made')).toBeTruthy()
  })

  it('shows an unavailable reply with the deterministic result', async () => {
    setup({
      [`GET ${CHAT}`]: () => [200, []],
      [`POST ${CHAT}`]: () => [200, exchange('unavailable', 'The LLM is disabled. The deterministic result for demo: One problem.', { reason: 'disabled' })],
    })
    await screen.findByText(/No messages yet/)
    type('Why?')

    fireEvent.click(sendButton())

    expect(await screen.findByText(/The deterministic result for demo/)).toBeTruthy()
    expect(screen.getByText('AI unavailable: deterministic result')).toBeTruthy()
  })
})

describe('ChatPanel errors', () => {
  it.each([
    [404, 'investigation_not_found', 'No investigation has this ID.', /no longer exists/],
    [409, 'chat_busy', 'A reply is still being prepared.', /A reply is still being prepared\. Your message was kept/],
    [409, 'investigation_running', 'Wait for the investigation to finish.', /Wait for the investigation to finish\. Your message was kept/],
    [422, null, null, /The message was not accepted/],
    [429, 'budget_exhausted', 'The local limit on AI requests was reached.', /local limit on AI requests was reached\. Your message was kept/],
    [503, 'storage_unavailable', 'The history could not be saved.', /could not be saved\. Your message was kept/],
    [500, null, null, /could not be sent/],
  ])('explains HTTP %s, keeps the message and does not resend it', async (status, code, text, expected) => {
    const body = code ? { detail: { code, message: text } } : { detail: [{ msg: 'String should have at most 2000 characters' }] }
    setup({ [`GET ${CHAT}`]: () => [200, []], [`POST ${CHAT}`]: () => [status, body] })
    await screen.findByText(/No messages yet/)
    type('Why is the pod failing?')

    fireEvent.click(sendButton())

    expect((await screen.findByRole('alert')).textContent).toMatch(expected)
    expect(input().value).toBe('Why is the pod failing?')
    expect(sendButton().disabled).toBe(false)
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(fake.calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })

  it('clears an earlier error when a message is sent successfully', async () => {
    let attempt = 0
    setup({
      [`GET ${CHAT}`]: () => [200, []],
      [`POST ${CHAT}`]: () => {
        attempt += 1
        return attempt === 1 ? [429, { detail: { code: 'budget_exhausted', message: 'Limit reached.' } }] : [200, exchange('answer', 'Answered.', { answer: chatAnswer() })]
      },
    })
    await screen.findByText(/No messages yet/)
    type('Why?')
    fireEvent.click(sendButton())
    await screen.findByRole('alert')

    fireEvent.click(sendButton())

    expect(await screen.findByText('Answered.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('ChatPanel requests', () => {
  it('only ever calls the chat endpoints of this investigation', async () => {
    setup({ [`GET ${CHAT}`]: () => [200, []], [`POST ${CHAT}`]: () => [200, exchange('answer', 'Fine.', { answer: chatAnswer() })] })
    await screen.findByText(/No messages yet/)
    type('Why?')
    fireEvent.click(sendButton())
    await screen.findByText('Fine.')

    expect(fake.calls.every((call) => call.path === CHAT)).toBe(true)
  })
})
