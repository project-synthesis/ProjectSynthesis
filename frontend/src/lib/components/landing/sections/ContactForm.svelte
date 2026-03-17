<script lang="ts">
  import type { ContactFormSection } from '$lib/content/types';

  interface Props {
    categories: ContactFormSection['categories'];
    successMessage: ContactFormSection['successMessage'];
  }

  let { categories, successMessage }: Props = $props();

  let submitted = $state(false);

  let name = $state('');
  let email = $state('');
  let category = $state('');
  let message = $state('');

  function handleSubmit(e: Event) {
    e.preventDefault();
    submitted = true;
  }
</script>

<div class="contact-form-wrapper" data-reveal>
  {#if submitted}
    <div class="contact-form__success">
      <span class="contact-form__check" aria-hidden="true">✓</span>
      <p class="contact-form__success-msg">{successMessage}</p>
    </div>
  {:else}
    <form class="contact-form" onsubmit={handleSubmit}>
      <div class="contact-form__field">
        <label class="contact-form__label" for="cf-name">Name</label>
        <input
          id="cf-name"
          type="text"
          class="contact-form__input"
          bind:value={name}
          required
          autocomplete="name"
        />
      </div>

      <div class="contact-form__field">
        <label class="contact-form__label" for="cf-email">Email</label>
        <input
          id="cf-email"
          type="email"
          class="contact-form__input"
          bind:value={email}
          required
          autocomplete="email"
        />
      </div>

      <div class="contact-form__field">
        <label class="contact-form__label" for="cf-category">Category</label>
        <select id="cf-category" class="contact-form__input" bind:value={category} required>
          <option value="" disabled selected>Select…</option>
          {#each categories as cat}
            <option value={cat}>{cat}</option>
          {/each}
        </select>
      </div>

      <div class="contact-form__field">
        <label class="contact-form__label" for="cf-message">Message</label>
        <textarea
          id="cf-message"
          class="contact-form__textarea"
          bind:value={message}
          required
        ></textarea>
      </div>

      <button type="submit" class="contact-form__submit">Send Message</button>
    </form>
  {/if}
</div>

<style>
  .contact-form-wrapper {
    max-width: 480px;
  }

  .contact-form {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .contact-form__field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .contact-form__label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-dim);
  }

  .contact-form__input {
    height: 20px;
    padding: 0 8px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-sans);
    font-size: 11px;
    outline: none;
    transition: border-color var(--duration-hover) var(--ease-spring);
    width: 100%;
    box-sizing: border-box;
  }

  .contact-form__input:focus {
    border-color: rgba(0, 229, 255, 0.3);
  }

  .contact-form__textarea {
    height: 80px;
    padding: 6px 8px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-sans);
    font-size: 11px;
    resize: vertical;
    outline: none;
    transition: border-color var(--duration-hover) var(--ease-spring);
    width: 100%;
    box-sizing: border-box;
    line-height: 1.5;
  }

  .contact-form__textarea:focus {
    border-color: rgba(0, 229, 255, 0.3);
  }

  .contact-form__submit {
    height: 24px;
    padding: 0 16px;
    background: var(--color-neon-cyan);
    border: 1px solid var(--color-neon-cyan);
    color: #06060c;
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    align-self: flex-start;
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .contact-form__submit:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 85%, white);
    border-color: var(--color-neon-cyan);
  }

  .contact-form__success {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 12px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .contact-form__check {
    font-size: 14px;
    color: var(--color-neon-green);
    flex-shrink: 0;
    line-height: 1;
  }

  .contact-form__success-msg {
    font-size: 12px;
    color: var(--color-text-secondary);
    margin: 0;
    line-height: 1.6;
  }
</style>
