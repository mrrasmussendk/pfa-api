@API
Feature: chunking

  Cuts a text into chunks that each fit a token budget, counted by the embedding model's own tokenizer.

  Scenario: split-within-budget
    Given a text of three sentences
    And a budget of 12 tokens
    When I send them to POST /chunking/split
    Then I receive 200
    And the text comes back as more than one chunk, in order
    And every chunk is counted at or below the 12-token budget

  Scenario: empty-text-is-a-problem
    Given a text that is only whitespace
    And a budget of 12 tokens
    When I send them to POST /chunking/split
    Then I receive 422
    And the body is an application/problem+json document
    And its detail says the text is empty

  Scenario: control-character-is-rejected
    Given a text that contains a NUL character
    And a budget of 12 tokens
    When I send them to POST /chunking/split
    Then I receive 422
    And the body is an application/problem+json document
    And its type is validation-error
    And its errors name body.text
    And the error on body.text says it contains a control character

  Scenario: budget-outside-the-window-is-rejected
    Given a text of one sentence
    And a budget of 513 tokens, one above the model's 512-token window
    When I send them to POST /chunking/split
    Then I receive 422
    And the body is an application/problem+json document
    And its type is validation-error
    And its errors name body.budget

  Scenario: unknown-field-is-rejected
    Given a text of one sentence
    And a field the API does not know, "budgt", in place of the budget
    When I send them to POST /chunking/split
    Then I receive 422
    And the body is an application/problem+json document
    And its type is validation-error
    And its errors name body.budgt
